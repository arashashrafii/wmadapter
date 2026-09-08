from __future__ import annotations

import importlib.util
import shutil
import json
import os
import subprocess


def run_openai_sdk(context, payload):
    if importlib.util.find_spec("openai") is None: return "BLOCKED", "official OpenAI SDK is unavailable"
    if not os.environ.get("MIMICGATE_LIVE_API_KEY"): return "BLOCKED", "MIMICGATE_LIVE_API_KEY is not configured"
    from openai import OpenAI
    response = OpenAI(api_key=os.environ["MIMICGATE_LIVE_API_KEY"], base_url=context.base_url).chat.completions.create(**payload)
    if not response.choices or response.choices[0].message.role != "assistant": return "FAIL", "typed SDK response has no assistant choice"
    return "PASS", "official OpenAI SDK typed response deserialized"


def run_openclaw(context, message: str, include_tools: bool = False):
    command = os.environ.get("MIMICGATE_OPENCLAW_COMMAND")
    config = os.environ.get("MIMICGATE_OPENCLAW_CONFIG")
    if not command or not config: return "BLOCKED", "MIMICGATE_OPENCLAW_COMMAND and MIMICGATE_OPENCLAW_CONFIG are required"
    try: args = json.loads(command); extra = json.loads(os.environ.get("MIMICGATE_OPENCLAW_TOOL_ARGS", "[]")) if include_tools else []
    except json.JSONDecodeError: return "BLOCKED", "OpenClaw command configuration must be a JSON argument list"
    if not isinstance(args, list) or not all(isinstance(item, str) for item in args): return "BLOCKED", "OpenClaw command must be a JSON string argument list"
    try:
        completed = subprocess.run(args + extra, input=message, text=True, capture_output=True, timeout=context.timeout, check=False)
    except (OSError, subprocess.TimeoutExpired) as error: return "BLOCKED", f"OpenClaw execution unavailable: {type(error).__name__}"
    output = (completed.stdout + "\n" + completed.stderr).lower()
    if completed.returncode != 0: return "FAIL", f"OpenClaw exited {completed.returncode}"
    if context.base_url.lower() not in output: return "FAIL", "OpenClaw output did not evidence the configured endpoint"
    return "PASS", "OpenClaw exited successfully with endpoint evidence"


def adapter_status(name: str) -> tuple[str, str]:
    available = importlib.util.find_spec("openai") is not None if name == "openai" else shutil.which("openclaw") is not None
    if not available:
        return "BLOCKED", f"{name} adapter is unavailable"
    return "available", "adapter detected"
