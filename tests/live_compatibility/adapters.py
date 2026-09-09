from __future__ import annotations

import importlib.util
import shutil
import json
import os
import subprocess
from pathlib import Path


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


def adapter_status(name: str) -> tuple[str, str]:
    available = importlib.util.find_spec("openai") is not None if name == "openai" else shutil.which("openclaw") is not None
    if not available:
        return "BLOCKED", f"{name} adapter is unavailable"
    return "available", "adapter detected"
