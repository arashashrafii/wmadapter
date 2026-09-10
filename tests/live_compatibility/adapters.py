from __future__ import annotations

import importlib.util
import shutil
import json
import os
import queue
import re
import subprocess
import tempfile
import threading
import time
from pathlib import Path


_STDERR_SENSITIVE_VALUE = re.compile(
    r"(?i)\b(authorization\s*[:=]\s*(?:bearer\s+)?|bearer\s+|"
    r"(?:api[_-]?key|token|secret|password|credential)\s*[:=]\s*)([^\s,;]+)"
)
_STDERR_DIAGNOSTIC_LIMIT = 500
_OPENCODE_PROMPT_PLACEHOLDER = "{prompt}"
_OPENCODE_PROMPT_LIMIT = 8_000
_OPENCODE_STDOUT_LIMIT = 1_000_000
_OPENCODE_STDERR_LIMIT = 1_000_000
_LOG_PROVIDER = re.compile(r"(?i)\bproviderID\s*[:=]\s*([A-Za-z0-9_.-]+)")
_LOG_MODEL = re.compile(r"(?i)\bmodelID\s*[:=]\s*([A-Za-z0-9_./:-]+)")


def _stderr_diagnostic(stderr: str) -> str:
    """Return a short diagnostic without exposing credential-like values."""
    sanitized = _STDERR_SENSITIVE_VALUE.sub(r"\1[REDACTED]", stderr or "")
    sanitized = " ".join(sanitized.split())
    if not sanitized:
        return "<empty>"
    if len(sanitized) > _STDERR_DIAGNOSTIC_LIMIT:
        return sanitized[:_STDERR_DIAGNOSTIC_LIMIT] + "…"
    return sanitized


def _opencode_prompt(payload: dict) -> tuple[str | None, str | None]:
    messages = payload.get("messages")
    if not isinstance(messages, list):
        return None, "OpenCode payload has no usable messages"
    for message in reversed(messages):
        if not isinstance(message, dict) or message.get("role") != "user":
            continue
        content = message.get("content")
        if isinstance(content, str):
            prompt = content
        elif isinstance(content, list):
            text_parts = [
                item.get("text", "")
                for item in content
                if isinstance(item, dict) and isinstance(item.get("text"), str)
            ]
            prompt = "\n".join(text_parts)
        else:
            return None, "OpenCode user message content is not text-compatible"
        if len(prompt) > _OPENCODE_PROMPT_LIMIT:
            return None, f"OpenCode prompt exceeds {_OPENCODE_PROMPT_LIMIT} characters"
        return prompt, None
    return None, "OpenCode payload has no user message"


def _opencode_args(args: list[str], payload: dict) -> tuple[list[str] | None, str | None]:
    prompt, error = _opencode_prompt(payload)
    if error:
        return None, error
    assert prompt is not None
    placeholder_count = args.count(_OPENCODE_PROMPT_PLACEHOLDER)
    if placeholder_count > 1:
        return None, "OpenCode command may contain at most one {prompt} placeholder"
    if placeholder_count == 1:
        return [prompt if item == _OPENCODE_PROMPT_PLACEHOLDER else item for item in args], None
    return [*args, prompt], None


def _client_evidence(stdout: str) -> dict | None:
    try:
        evidence = json.loads(stdout)
    except json.JSONDecodeError:
        events = []
        for line in stdout.splitlines():
            if not line.strip():
                continue
            try:
                events.append(json.loads(line))
            except json.JSONDecodeError:
                return None
        evidence = next(
            (event for event in reversed(events) if isinstance(event, dict) and {"provider", "model", "status"} <= event.keys()),
            events[-1] if events else None,
        )
    return evidence if isinstance(evidence, dict) else None


def _opencode_log_evidence(stderr: str) -> tuple[str, str] | None:
    provider = model = None
    for line in stderr.splitlines():
        safe_line = _STDERR_SENSITIVE_VALUE.sub(r"\1[REDACTED]", line)
        provider_match = _LOG_PROVIDER.search(safe_line)
        model_match = _LOG_MODEL.search(safe_line)
        if provider_match:
            provider = provider_match.group(1)
        if model_match:
            model = model_match.group(1)
    return (provider, model) if provider and model else None


def _opencode_tool_loop(stdout: str) -> tuple[bool, bool]:
    invoked = completed = False
    call_ids: set[str] = set()
    for line in stdout.splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        part = event.get("part") or event.get("properties", {}).get("part") or {}
        tool = event.get("tool") or event.get("name")
        if isinstance(part, dict):
            tool = part.get("tool") or part.get("name") or tool
        is_tool_use = event.get("type") in {"tool_use", "tool_call"} or (isinstance(part, dict) and part.get("type") in {"tool", "tool-use", "tool_call"})
        call_id = event.get("callID") or event.get("call_id") or (part.get("callID") if isinstance(part, dict) else None)
        if is_tool_use and tool == "bash":
            invoked = True
            if call_id:
                call_ids.add(str(call_id))
        state = part.get("state") if isinstance(part, dict) else None
        state_status = state.get("status") if isinstance(state, dict) else None
        is_result = event.get("type") in {"tool_result", "tool_end", "tool_finish"} or (isinstance(part, dict) and part.get("type") in {"tool_result", "tool-result"})
        if is_result or state_status in {"completed", "success"}:
            if not call_id or str(call_id) in call_ids:
                completed = True
    return invoked, completed


def _opencode_terminal_event(event: dict, pending_tool_calls: set[str]) -> bool:
    event_type = event.get("type")
    part = event.get("part") or event.get("properties", {}).get("part") or {}
    part_type = part.get("type") if isinstance(part, dict) else None
    call_id = event.get("callID") or event.get("call_id") or (part.get("callID") if isinstance(part, dict) else None)
    if event_type in {"tool_use", "tool_call", "tool_start"} or part_type in {"tool", "tool-use", "tool_call"}:
        if call_id:
            pending_tool_calls.add(str(call_id))
    if event_type in {"tool_result", "tool_end", "tool_finish"} or part_type in {"tool_result", "tool-result"}:
        if call_id:
            pending_tool_calls.discard(str(call_id))
    state = part.get("state") if isinstance(part, dict) else None
    if call_id and isinstance(state, dict) and state.get("status") in {"completed", "success"}:
        pending_tool_calls.discard(str(call_id))
    reason = event.get("reason")
    if isinstance(part, dict):
        reason = part.get("reason", reason)
    return event_type == "step_finish" and reason == "stop" and not pending_tool_calls


def _terminate_process(process: subprocess.Popen) -> None:
    if process.poll() is not None:
        return
    try:
        process.terminate()
        process.wait(timeout=1)
        return
    except (OSError, subprocess.TimeoutExpired):
        pass
    if os.name != "nt":
        try:
            os.killpg(process.pid, 9)
        except OSError:
            pass
    else:
        process.kill()
    try:
        process.wait(timeout=1)
    except (OSError, subprocess.TimeoutExpired):
        pass


def _run_opencode(args: list[str], *, env: dict[str, str], cwd: str, timeout: float) -> tuple[subprocess.CompletedProcess[str], bool]:
    process = subprocess.Popen(
        args,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        shell=False,
        env=env,
        cwd=cwd,
        start_new_session=True,
    )
    events: list[str] = []
    diagnostics: list[str] = []
    pending_tool_calls: set[str] = set()
    messages: queue.Queue[tuple[str, str | None]] = queue.Queue()

    def read_stream(name: str, stream) -> None:
        try:
            for line in iter(stream.readline, ""):
                messages.put((name, line))
        finally:
            messages.put((name, None))

    streams = [("stdout", process.stdout), ("stderr", process.stderr)]
    threads = [threading.Thread(target=read_stream, args=item, daemon=True) for item in streams]
    for thread in threads:
        thread.start()
    closed_streams = 0
    captured_stdout = 0
    terminal = False
    deadline = time.monotonic() + timeout
    try:
        while time.monotonic() < deadline:
            if closed_streams == len(streams) and process.poll() is not None and messages.empty():
                break
            try:
                name, line = messages.get(timeout=min(0.1, max(0.0, deadline - time.monotonic())))
            except queue.Empty:
                continue
            if line is None:
                closed_streams += 1
                continue
            if name == "stdout":
                if captured_stdout < _OPENCODE_STDOUT_LIMIT:
                    retained = line[: _OPENCODE_STDOUT_LIMIT - captured_stdout]
                    events.append(retained)
                    captured_stdout += len(retained)
                try:
                    terminal = _opencode_terminal_event(json.loads(line), pending_tool_calls)
                except json.JSONDecodeError:
                    pass
                if terminal:
                    _terminate_process(process)
                    break
            elif sum(map(len, diagnostics)) < _OPENCODE_STDERR_LIMIT:
                diagnostics.append(line[: _OPENCODE_STDERR_LIMIT - sum(map(len, diagnostics))])
        else:
            _terminate_process(process)
            raise subprocess.TimeoutExpired(args, timeout, output="".join(events), stderr="".join(diagnostics))
    finally:
        if not terminal and process.poll() is None:
            _terminate_process(process)
        for thread in threads:
            thread.join(timeout=0.5)
        while True:
            try:
                name, line = messages.get_nowait()
            except queue.Empty:
                break
            if line is None:
                continue
            if name == "stdout":
                if captured_stdout < _OPENCODE_STDOUT_LIMIT:
                    retained = line[: _OPENCODE_STDOUT_LIMIT - captured_stdout]
                    events.append(retained)
                    captured_stdout += len(retained)
            elif sum(map(len, diagnostics)) < _OPENCODE_STDERR_LIMIT:
                diagnostics.append(line[: _OPENCODE_STDERR_LIMIT - sum(map(len, diagnostics))])
        for stream in (process.stdout, process.stderr):
            if stream is not None:
                stream.close()
    return subprocess.CompletedProcess(args, 0 if terminal else process.returncode, "".join(events), "".join(diagnostics)), terminal


def run_openai_sdk(context, payload):
    if importlib.util.find_spec("openai") is None: return "BLOCKED", "official OpenAI SDK is unavailable"
    if not os.environ.get("WMADAPTER_LIVE_API_KEY"): return "BLOCKED", "WMADAPTER_LIVE_API_KEY is not configured"
    from openai import OpenAI
    try:
        response = OpenAI(api_key=os.environ["WMADAPTER_LIVE_API_KEY"], base_url=context.base_url).chat.completions.create(**payload)
    except Exception as error:
        status = getattr(error, "status_code", None)
        if status in {401, 408, 429, 502, 503, 504}:
            return "BLOCKED", f"official OpenAI SDK prerequisite unavailable (HTTP {status})"
        return "FAIL", f"official OpenAI SDK request failed: {type(error).__name__}"
    if not response.choices or response.choices[0].message.role != "assistant": return "FAIL", "typed SDK response has no assistant choice"
    return "PASS", "official OpenAI SDK typed response deserialized"


def run_openclaw(context, message: str, include_tools: bool = False):
    command = os.environ.get("WMADAPTER_OPENCLAW_COMMAND")
    config = os.environ.get("WMADAPTER_OPENCLAW_CONFIG")
    if not command or not config: return "BLOCKED", "WMADAPTER_OPENCLAW_COMMAND and WMADAPTER_OPENCLAW_CONFIG are required"
    if not Path(config).is_file(): return "BLOCKED", "configured OpenClaw config file is unavailable"
    try: args = json.loads(command); extra = json.loads(os.environ.get("WMADAPTER_OPENCLAW_TOOL_ARGS", "[]")) if include_tools else []
    except json.JSONDecodeError: return "BLOCKED", "OpenClaw command configuration must be a JSON argument list"
    if not isinstance(args, list) or not all(isinstance(item, str) for item in args): return "BLOCKED", "OpenClaw command must be a JSON string argument list"
    try:
        completed = subprocess.run(args + extra, input=message, text=True, capture_output=True, timeout=context.timeout, check=False)
    except (OSError, subprocess.TimeoutExpired) as error: return "BLOCKED", f"OpenClaw execution unavailable: {type(error).__name__}"
    if completed.returncode != 0: return "FAIL", f"OpenClaw exited {completed.returncode}"
    try:
        result = json.loads(completed.stdout)
    except json.JSONDecodeError:
        return "FAIL", "OpenClaw did not return JSON execution evidence"
    meta = result.get("result", {}).get("meta", {}).get("agentMeta", {})
    if result.get("status") != "ok": return "FAIL", "OpenClaw reported an unsuccessful run"
    if meta.get("provider") != "wmadapter" or meta.get("model") != context.model:
        return "FAIL", "OpenClaw execution evidence did not identify the configured provider/model"
    if not result.get("result", {}).get("payloads"): return "FAIL", "OpenClaw returned no response payload"
    return "PASS", "OpenClaw completed with verified provider/model execution evidence"


def run_client_case(context, case, payload: dict):
    """Run an explicitly configured client harness; never guesses CLI flags."""
    prefix = case.client.upper()
    command = os.environ.get(f"WMADAPTER_{prefix}_COMMAND")
    config = os.environ.get(f"WMADAPTER_{prefix}_CONFIG")
    if not command or not config:
        return "BLOCKED", f"WMADAPTER_{prefix}_COMMAND and WMADAPTER_{prefix}_CONFIG are required"
    if not Path(config).is_file():
        return "BLOCKED", f"configured {case.client} config file is unavailable"
    try:
        args = json.loads(command)
    except json.JSONDecodeError:
        return "BLOCKED", f"{case.client} command configuration must be a JSON argument list"
    if not isinstance(args, list) or not all(isinstance(item, str) for item in args):
        return "BLOCKED", f"{case.client} command must be a JSON string argument list"
    request = json.dumps({"case_id": case.case_id, "kind": case.kind, "payload": payload}, ensure_ascii=False)
    client_args = args
    client_input = request
    if case.client.lower() == "opencode":
        client_args, argument_error = _opencode_args(args, payload)
        if argument_error:
            return "BLOCKED", argument_error
        client_input = None
    with tempfile.TemporaryDirectory(prefix=f"wmadapter-{prefix.lower()}-data-") as data_home, tempfile.TemporaryDirectory(
        prefix=f"wmadapter-{prefix.lower()}-runtime-"
    ) as runtime_home:
        os.chmod(runtime_home, 0o700)
        client_env = os.environ.copy()
        client_env["XDG_DATA_HOME"] = data_home
        client_env["XDG_RUNTIME_DIR"] = runtime_home
        run_options = {
            "input": client_input,
            "text": True,
            "capture_output": True,
            "timeout": context.timeout,
            "check": False,
            "shell": False,
            "env": client_env,
        }
        if case.client.lower() == "opencode":
            try:
                completed, terminal = _run_opencode(
                    client_args,
                    env=client_env,
                    cwd=str(Path(config).resolve().parent),
                    timeout=context.timeout,
                )
            except (OSError, subprocess.TimeoutExpired) as error:
                return "BLOCKED", f"{case.client} execution unavailable: {type(error).__name__}"
        else:
            terminal = True
            try:
                completed = subprocess.run(client_args, **run_options)
            except (OSError, subprocess.TimeoutExpired) as error:
                return "BLOCKED", f"{case.client} execution unavailable: {type(error).__name__}"
    if completed.returncode != 0:
        diagnostic = _stderr_diagnostic(completed.stderr)
        return "FAIL", f"{case.client} exited {completed.returncode} (stderr: {diagnostic})"
    if case.client.lower() == "opencode":
        if not terminal:
            return "FAIL", f"{case.client} did not emit a terminal step_finish event"
        if "--print-logs" not in client_args:
            return "FAIL", f"{case.client} requires --print-logs for provider/model evidence"
        log_evidence = _opencode_log_evidence(completed.stderr)
        if log_evidence != ("wmadapter", context.model):
            return "FAIL", f"{case.client} logs did not identify the configured provider/model"
        evidence = _client_evidence(completed.stdout)
    else:
        try:
            evidence = json.loads(completed.stdout)
        except json.JSONDecodeError:
            evidence = None
    if evidence is None:
        return "FAIL", f"{case.client} did not return JSON execution evidence"
    if case.client.lower() != "opencode" and (evidence.get("provider") != "wmadapter" or evidence.get("model") != context.model):
        return "FAIL", f"{case.client} evidence did not identify the configured provider/model"
    if case.client.lower() != "opencode":
        if case.expected_status == 200 and evidence.get("status") != "ok":
            return "FAIL", f"{case.client} reported an unsuccessful run"
        if case.expected_status >= 400 and evidence.get("status") != "expected_error":
            return "FAIL", f"{case.client} did not report the expected controlled error"
    if case.kind == "sse" and case.client.lower() != "opencode" and evidence.get("stream_classification") not in {"buffered", "progressive"}:
        return "FAIL", f"{case.client} omitted SSE classification"
    if case.kind == "tool_roundtrip" and evidence.get("tool_round_trip") is not True:
        if case.client.lower() != "opencode" or _opencode_tool_loop(completed.stdout) != (True, True):
            return "FAIL", f"{case.client} did not verify the tool round trip"
    if case.client.lower() == "opencode" and case.kind == "tool_roundtrip":
        return "PASS", f"{case.client} completed client tool loop with terminal provider/model evidence"
    if case.client.lower() == "opencode" and case.kind == "sse":
        return "PASS", f"{case.client} completed stream with terminal provider/model evidence"
    return "PASS", f"{case.client} completed {case.kind} with provider/model evidence"


def adapter_status(name: str) -> tuple[str, str]:
    available = importlib.util.find_spec("openai") is not None if name == "openai" else shutil.which("openclaw") is not None
    if not available:
        return "BLOCKED", f"{name} adapter is unavailable"
    return "available", "adapter detected"
