from __future__ import annotations

import importlib.util
import shutil
import json
import os
import re
import subprocess
import tempfile
from pathlib import Path


_STDERR_SENSITIVE_VALUE = re.compile(
    r"(?i)\b(authorization\s*[:=]\s*(?:bearer\s+)?|bearer\s+|"
    r"(?:api[_-]?key|token|secret|password|credential)\s*[:=]\s*)([^\s,;]+)"
)
_STDERR_DIAGNOSTIC_LIMIT = 500
_OPENCODE_PROMPT_PLACEHOLDER = "{prompt}"
_OPENCODE_PROMPT_LIMIT = 8_000


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
        evidence = events[-1] if events else None
    return evidence if isinstance(evidence, dict) else None


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
            run_options["stdin"] = subprocess.DEVNULL
            run_options["cwd"] = str(Path(config).resolve().parent)
        try:
            completed = subprocess.run(client_args, **run_options)
        except (OSError, subprocess.TimeoutExpired) as error:
            return "BLOCKED", f"{case.client} execution unavailable: {type(error).__name__}"
    if completed.returncode != 0:
        diagnostic = _stderr_diagnostic(completed.stderr)
        return "FAIL", f"{case.client} exited {completed.returncode} (stderr: {diagnostic})"
    if case.client.lower() == "opencode":
        evidence = _client_evidence(completed.stdout)
    else:
        try:
            evidence = json.loads(completed.stdout)
        except json.JSONDecodeError:
            evidence = None
    if evidence is None:
        return "FAIL", f"{case.client} did not return JSON execution evidence"
    if evidence.get("provider") != "wmadapter" or evidence.get("model") != context.model:
        return "FAIL", f"{case.client} evidence did not identify the configured provider/model"
    if case.expected_status == 200 and evidence.get("status") != "ok":
        return "FAIL", f"{case.client} reported an unsuccessful run"
    if case.expected_status >= 400 and evidence.get("status") != "expected_error":
        return "FAIL", f"{case.client} did not report the expected controlled error"
    if case.kind == "sse" and evidence.get("stream_classification") not in {"buffered", "progressive"}:
        return "FAIL", f"{case.client} omitted SSE classification"
    if case.kind == "tool_roundtrip" and evidence.get("tool_round_trip") is not True:
        return "FAIL", f"{case.client} did not verify the tool round trip"
    return "PASS", f"{case.client} completed {case.kind} with provider/model evidence"


def adapter_status(name: str) -> tuple[str, str]:
    available = importlib.util.find_spec("openai") is not None if name == "openai" else shutil.which("openclaw") is not None
    if not available:
        return "BLOCKED", f"{name} adapter is unavailable"
    return "available", "adapter detected"
