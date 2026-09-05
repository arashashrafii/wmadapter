"""Shared legacy Web text protocol; no browser or HTTP dependencies."""
from __future__ import annotations

import hashlib
import json
import re
import uuid
from typing import Any
from .contract import Message
from .policy import ClientPolicy, detect_client_policy

def _content_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict):
                value = item.get("text") or item.get("content")
                if isinstance(value, str):
                    parts.append(value)
        return "\n".join(parts)
    if content is None:
        return ""
    return str(content)


def _image_attachments(messages: list[Message]) -> list[str]:
    """Return OpenAI data-URL images for upload to the Web chat.

    Remote URLs are intentionally ignored: fetching arbitrary URLs in the
    gateway would create an SSRF surface. The browser adapter only needs the
    data URLs emitted by OpenClaw attachments.
    """
    attachments: list[str] = []
    def collect(value: Any) -> None:
        if isinstance(value, list):
            for item in value:
                collect(item)
            return
        if not isinstance(value, dict):
            return
        image_url = value.get("image_url")
        url = image_url.get("url") if isinstance(image_url, dict) else None
        if not isinstance(url, str):
            source = value.get("source")
            url = source.get("url") if isinstance(source, dict) else None
            if not isinstance(url, str) and isinstance(source, dict):
                data = source.get("data")
                media_type = source.get("media_type") or source.get("mediaType")
                if isinstance(data, str) and isinstance(media_type, str):
                    url = f"data:{media_type};base64,{data}"
        if not isinstance(url, str) and isinstance(value.get("data"), str):
            data = value["data"]
            if data.startswith("data:image/"):
                url = data
            else:
                media_type = value.get("mime_type") or value.get("mimeType") or "image/png"
                if value.get("type") in {"image", "input_image"} and isinstance(media_type, str):
                    url = f"data:{media_type};base64,{data}"
        if isinstance(url, str) and url.startswith("data:image/"):
            attachments.append(url)
            return
        # OpenClaw may wrap media in parts/files/attachments objects. Recurse
        # only through container values so arbitrary text cannot be treated as
        # a URL or fetched by the gateway.
        for key in ("content", "parts", "files", "attachments", "images", "image", "media"):
            if key in value:
                collect(value[key])

    for message in messages:
        collect(message.model_dump(exclude={"role", "name"}))
    return attachments


def _prompt(messages: list[Message], system_prompt: str = "", tools: list[dict[str, Any]] | None = None,
            client_policy: ClientPolicy | None = None) -> str:
    lines = []
    if system_prompt.strip() and not any(message.role.lower() == "system" for message in messages):
        lines.append(f"SYSTEM: {system_prompt.strip()}")
    for message in messages:
        text = _content_text(message.content).strip()
        if message.role.lower() == "assistant" and getattr(message, "tool_calls", None):
            for call in message.tool_calls:
                function = call.get("function", {}) if isinstance(call, dict) else {}
                lines.append("ASSISTANT_TOOL_CALL: " + json.dumps({
                    "name": function.get("name", ""),
                    "id": call.get("id", ""),
                    "arguments": function.get("arguments", "{}"),
                }, ensure_ascii=False))
            if text:
                lines.append(f"ASSISTANT: {text}")
        elif message.role.lower() == "tool":
            lines.append(f"TOOL_RESULT ({getattr(message, 'tool_call_id', '')}): {text}")
        elif text:
            lines.append(f"{message.role.upper()}: {text}")
    if messages and not _is_title_request(messages):
        lines.append(
            "UNIVERSAL RELIABILITY LOOP: For every request, seek the most accurate answer through a repeatable "
            "cycle: decompose the question, identify what must be known, gather evidence from available tools or "
            "authoritative sources, reason or calculate explicitly, cross-check calculations and conclusions, and "
            "compare the result with observable reality. If evidence conflicts, is incomplete, or the requested "
            "state is not achieved, investigate the discrepancy and retry with a corrected approach. Never invent "
            "facts, tool results, sources, commands, or success. Distinguish observed facts, inferences, and "
            "assumptions; state uncertainty when verification is unavailable. For action requests, do not finish "
            "until the requested outcome is freshly verified or a concrete blocker is proven."
        )
    if tools:
        tool_text = json.dumps(tools, ensure_ascii=False, separators=(",", ":"))
        lines.append(
            "FINAL TOOL PROTOCOL: You may use one listed tool. If a tool is required, output only "
            "<tool_call>{\"name\":\"tool_name\",\"arguments\":{...}}</tool_call>. "
            "Do not describe a tool call and do not execute commands in text. "
            "Available tools: " + tool_text
        )
        lines.append(
            "OPENCLAW CAPABILITY POLICY: Treat the listed tool schemas as the only callable capabilities. "
            "Use the tool whose description best matches the user's goal: use browser for browser/web automation, "
            "read/write/edit/apply_patch for files, exec/process for system work, and session/agent tools for delegation. "
            "Skills provide workflow instructions and plugins provide extra tools; use them when a matching capability "
            "is listed or its instructions are present. Do not invent plugin or skill behavior. Never claim that a task "
            "was completed before receiving its tool result. After a tool result, continue the workflow or give the "
            "final answer. Keep tool protocol markers and code-renderer labels out of the final answer."
        )
        tool_names = {
            item.get("function", {}).get("name")
            for item in tools
            if isinstance(item, dict) and isinstance(item.get("function"), dict)
        }
        conversation_text = " ".join(_content_text(message.content) for message in messages).lower()
        selected_policy = client_policy if client_policy is not None else detect_client_policy(messages, tools)
        openclaw_request = selected_policy is ClientPolicy.OPENCLAW
        if openclaw_request:
            lines.append(
                "OPENCLAW DOCUMENTATION POLICY: This request concerns OpenClaw. Before choosing a solution, "
                "search the official documentation at https://docs.openclaw.ai/ and use the relevant current page "
                "or built-in help as evidence. Do not rely on memory, stale conversation history, or guessed command "
                "names. If the browser tool is listed, search docs.openclaw.ai with it before acting; if it is not "
                "listed, use available OpenClaw help/status tools and clearly distinguish verified facts from blockers."
            )
            lines.append(
                "RESEARCH-ACTION-VERIFICATION LOOP: Resolve OpenClaw problems as a loop, not a one-shot answer: "
                "(1) identify the missing capability, (2) research the official docs or built-in help, (3) return the "
                "research result to the reasoning context, (4) send the verified action to the appropriate OpenClaw "
                "tool, (5) inspect the fresh result, and (6) retry with a corrected action when verification fails. "
                "Continue until the user's requested outcome is verified or a specific approval, pairing, login, or "
                "unavailable capability blocks progress. Use Google search only as a fallback when the official docs "
                "and built-in help do not answer the question, and label that source and uncertainty explicitly."
            )
        lines.append(
            "OPENCLAW CAPABILITY CHECK: When a request depends on an OpenClaw node, plugin, skill, channel, "
            "computer capability, or desktop feature, first inspect the currently available tool schemas and the "
            "relevant built-in help/documentation if a tool can access it. If proposing a CLI command, verify the "
            "exact command and its output in the current environment before presenting or executing it. Do not guess "
            "or present an unverified command as a solution. Every user request that asks for an action must end with "
            "a fresh verification of the requested result, or a concrete, evidence-backed blocker."
        )
        lines.append(
            "SELF-REMEDIATION: Before reporting a blocker, attempt safe remediation using the listed capabilities: "
            "inspect the relevant OpenClaw status/help, correct an incomplete local setup when the current tool and "
            "permissions allow it, and retry the requested action. Do not change unrelated projects, credentials, "
            "browser data, or security policy. After each remediation attempt, verify that remediation changed the "
            "capability and retest the original request. Stop only when the result is verified or the remaining step "
            "requires an explicit human approval, pairing, login, or unavailable capability; state that exact blocker."
        )
        if "computer" in tool_names:
            lines.append(
                "DESKTOP GUI POLICY: For a request to open or operate a desktop application's graphical interface, "
                "use the listed `computer` capability for screenshot/observation and pointer/keyboard actions. "
                "Use fresh screen.snapshot evidence before and after each meaningful UI action. Do not substitute "
                "cvlc, `--intf dummy`, `--play-and-exit`, a background-only process, or shell narration for GUI control. "
                "Launch at most one instance, then observe its window and verify the requested UI effect before claiming success."
            )
        else:
            lines.append(
                "DESKTOP GUI POLICY: If the user requests clicking, typing, or controlling a desktop application's UI "
                "and no `computer` tool is listed, do not claim GUI control and do not substitute cvlc, a headless mode, "
                "or a shell/code block. Report that the required computer capability is unavailable."
            )
        lines.append(
            "SELF-CORRECTION WORKFLOW: For every action-oriented request, follow "
            "ACTION -> OBSERVE RESULT -> VERIFY. After creating, editing, deleting, or opening something, "
            "inspect or query the resulting state before claiming success. A submitted command is not evidence "
            "that its effect occurred. If verification fails, correct the action and try again; continue until "
            "the requested outcome is verified or clearly report the concrete blocker. Never report an application "
            "opened without observable evidence such as a process, window, or workspace state. For generated files, "
            "read the relevant files and run an appropriate validation before declaring the work complete."
        )
        lines.append(
            "APPLICATION VERIFICATION EXAMPLE: For VS Code, use `code --status` and require evidence of the target "
            "window and its `Workspace Stats` folder before reporting it open. `code --list-extensions` is not proof "
            "that a VS Code window or workspace is open."
        )
        lines.append(
            "PENDING TOOL RESULTS: If a tool reports `Command still running`, use the available process tool to "
            "poll or retrieve its final output, then continue verification. Do not treat a pending command as failure "
            "or success, and do not stop the workflow while its result is available to collect."
        )
        lines.append(
            "MANDATORY FOLLOW-UP: If evidence is missing, emit the next tool call now using the exact tool protocol. "
            "Do not merely say that you will verify, do not narrate a planned action, and do not return a final "
            "answer until the requested state is verified or a concrete blocker is proven."
        )
        lines.append(
            "STRUCTURED TOOL CALLS: Never use a Bash/code block as a substitute for a tool call. For an action, "
            "emit the exact `<tool_call>` marker with the listed tool and arguments. Do not recommend or execute "
            "destructive commands such as kill, pkill, rm, or reset unless the user explicitly confirms that action."
        )
        lines.append(
            "EVIDENCE FRESHNESS: Treat prior assistant claims, PIDs, and suggested commands as unverified history, "
            "not as tool evidence. For the current user request, emit the tool call before any explanation and use "
            "fresh output from the current tool run; never reuse a stale process ID or old error as the current state."
        )
        lines.append(
            "LAUNCH ORDER: Research and capability checks may precede an authorized launch. "
            "Once prerequisites are verified, perform the launch and observe the requested window. A status or `which` "
            "check alone does not perform the requested launch and is never evidence that it happened."
        )
        if any(item.get("function", {}).get("name") == "browser" for item in tools):
            lines.append(
                "BROWSER TOOL SHAPE: For browser action=act and kind=fill, always send "
                "fields:[{ref:<textbox ref>,text:<value>}]. Never send fill ref/text as top-level fields."
            )
        if any(message.role.lower() == "tool" for message in messages):
            lines.append(
                "FINAL TOOL PROTOCOL: A tool result is already available above. "
                "Use it to answer the user; call another tool only if the result is insufficient."
            )
    return "\n\n".join(lines)


def _is_title_request(messages: list[Message]) -> bool:
    for message in messages:
        if message.role.lower() != "system":
            continue
        text = _content_text(message.content).lower()
        if "generate a concise session title" in text:
            return True
    return False


def _local_title(messages: list[Message]) -> str:
    for message in messages:
        if message.role.lower() != "user":
            continue
        text = " ".join(_content_text(message.content).split())
        if text:
            return text[:60].rstrip()
    return "New conversation"


def _fallback_conversation_id(messages: list[Message]) -> str | None:
    """Create a stable key for clients that omit OpenClaw session headers."""
    for message in messages:
        if message.role.lower() != "user":
            continue
        text = " ".join(_content_text(message.content).split())
        if text:
            digest = hashlib.sha256(text.encode("utf-8")).hexdigest()[:24]
            return f"auto:{digest}"
    return None


def _fallback_after_tool(messages: list[Message], answer: str) -> str:
    """Keep OpenClaw from receiving an empty assistant turn after a tool."""
    if answer.strip():
        return answer
    tool_activity = False
    for message in reversed(messages):
        role = message.role.lower()
        if role == "tool" or (role == "assistant" and getattr(message, "tool_calls", None)):
            tool_activity = True
        if role in {"tool", "tool_result"}:
            result = _content_text(message.content).strip()
            if result:
                return f"Tool result:\n{result}"
    return "No tool result is available; completion is not verified." if tool_activity else answer


async def _resolve_web_answer(provider, answer, messages, tools, conversation_id, prompt):
    """Repair a failed model response once; OpenClaw alone executes tools."""
    call, visible = _extract_tool_call(answer, tools)
    def violates_explicit_gui_constraint(candidate):
        if not candidate or not tools:
            return False
        function = candidate.get("function", {})
        if function.get("name") not in {"exec", "process"}:
            return False
        arguments = function.get("arguments", "")
        command = arguments if isinstance(arguments, str) else json.dumps(arguments, ensure_ascii=False)
        user_text = " ".join(
            _content_text(message.content).lower()
            for message in messages
            if message.role.lower() == "user"
        )
        gui_request = any(term in user_text for term in ("رابط گرافیکی", "رابط کاربری", "graphical interface", "gui"))
        explicitly_forbidden = any(term in user_text for term in ("cvlc", "--no-video", "headless"))
        return gui_request and explicitly_forbidden and any(term in command.lower() for term in ("cvlc", "--no-video", "--intf dummy"))
    def repeated(candidate):
        if not candidate:
            return False
        recent = []
        pending = {}
        for message in messages:
            if message.role == "user":
                recent.clear()
                pending.clear()
            elif message.role == "assistant":
                for item in getattr(message, "tool_calls", None) or []:
                    pending[item.get("id")] = item.get("function")
            elif message.role == "tool":
                function = pending.pop(getattr(message, "tool_call_id", None), None)
                if function:
                    recent.append((function, _content_text(message.content)))
        def signature(function):
            try:
                return (function.get("name"), json.loads(function.get("arguments", "{}")))
            except (ValueError, TypeError):
                return None
        # Polling is intentionally exempt: unchanged state is normal for running work.
        return (candidate["function"]["name"] != "process" and len(recent) >= 2
                and recent[-1][1] == recent[-2][1]
                and signature(candidate["function"]) == signature(recent[-1][0])
                == signature(recent[-2][0]))
    def needs_repair(text):
        return not text.strip() or bool(tools and re.search(
            r"<tool_call>|^\s*Action Input:", text, re.MULTILINE
        ))
    def is_action_request():
        action_terms = (
            "open", "launch", "start", "play", "click", "type", "run", "create", "delete",
            "execute", "control", "باز کن", "بازکردن", "اجرا", "پخش", "کلیک", "بنویس", "ایجاد",
            "حذف", "کنترل", "انجام بده", "تست کن",
        )
        return any(
            message.role.lower() == "user"
            and any(term in _content_text(message.content).lower() for term in action_terms)
            for message in messages
        )
    def is_narrated_plan(text):
        # Models sometimes describe the next action instead of emitting the
        # structured call OpenClaw needs. Treat that as incomplete only for an
        # action request; explanatory answers must remain ordinary prose.
        plan_terms = (
            "i will", "i'll", "first", "then", "finally", "ابتدا", "سپس", "در نهایت",
            "بررسی می‌کنم", "استفاده می‌کنم", "انجام می‌دهم", "خواهم کرد",
        )
        return bool(tools and is_action_request() and sum(term in text.lower() for term in plan_terms) >= 2)
    forbidden_gui_call = violates_explicit_gui_constraint(call)
    if forbidden_gui_call:
        call = None
        visible = ""
    if repeated(call) or forbidden_gui_call or (not call and (needs_repair(answer) or is_narrated_plan(answer))):
        repair_prompt = (
            prompt + "\n\nPROTOCOL REPAIR: Your previous response was empty or contained an "
            "unresolved tool instruction. No tool was executed from that response. Reconsider the "
            "current request using the tool results above. Return one valid listed tool call if "
            "another step is needed, or a supported final answer or precise approval request. "
            "If the user requested an action and the requested state is not verified, emit the "
            "structured tool call immediately; do not narrate a plan or describe what you will do. "
            "The previous call also violated an explicit GUI constraint; never emit a prohibited "
            "headless or cvlc command. Use the listed computer tool for GUI work. "
            "Do not repeat actions already confirmed by tool results. Do not invent evidence."
            " An identical tool call after two identical results is not progress: inspect the "
            "results, research a different approach, or report the supported result/blocker."
        )
        answer = await provider.complete(repair_prompt, conversation_id=conversation_id)
        call, visible = _extract_tool_call(answer, tools)
        if repeated(call) or (not call and needs_repair(answer)):
            raise ValueError("Web model failed to produce a valid response after one repair; completion is unverified")
    if not call:
        visible = _clean_renderer_artifacts(visible)
    return call, visible


def _clean_renderer_artifacts(answer: str) -> str:
    """Normalize labels injected by DeepSeek Web's code-block renderer."""
    code_languages = {
        "bash", "c", "c++", "c#", "csharp", "cpp", "cs", "css", "dart", "go", "html", "ini",
        "java", "javascript", "json", "kotlin", "lua", "markdown", "php", "plaintext",
        "mermaid", "powershell", "ps1", "python", "ruby", "rust", "scala", "shell", "sql", "svg", "swift",
        "text", "toml", "typescript", "xml", "xhtml", "yaml", "yml",
    }

    def looks_like_code(language: str, body: list[str]) -> bool:
        if not body:
            return False
        first = body[0].lstrip()
        if language in {"json", "html", "xml", "xhtml", "svg", "yaml", "yml", "toml", "ini", "css"}:
            return first.startswith(("{", "[", "<", "---", "<?xml", "<!DOCTYPE")) or "=" in first or ":" in first
        if language == "sql":
            return first.upper().startswith(("SELECT ", "INSERT ", "UPDATE ", "DELETE ", "CREATE ", "ALTER ", "WITH "))
        if language == "mermaid":
            return first.startswith(("graph ", "flowchart ", "sequenceDiagram", "classDiagram", "stateDiagram",
                                     "erDiagram", "gantt", "pie", "journey", "mindmap", "timeline", "xychart-beta"))
        if language in {"bash", "shell", "powershell", "ps1"}:
            return True
        return first.startswith(("#", "//", "/*", "<!--", "$", "using ", "namespace ", "public ", "private ",
                                "import ", "from ", "def ", "class ", "function ", "const ", "let ", "var ",
                                "print(", "echo ", "#!/", "package ", "fn ", "func ", "Console."))

    def text_chart_body(body: list[str]) -> tuple[list[str], list[str]]:
        markers = ("┤", "├", "└", "┘", "┌", "┐", "│", "─", "▇", "█", "■", "□")
        marker_indexes = [i for i, line in enumerate(body) if any(marker in line for marker in markers)]
        if len(body) < 3 or not marker_indexes:
            return [], body
        start, end = marker_indexes[0], marker_indexes[-1]
        chart = body[:start] + body[start:end + 1]
        remainder = body[end + 1:]
        tail = []
        while remainder and remainder[0].strip():
            tail.append(remainder.pop(0))
        if tail:
            chart.extend(tail)
        while remainder and not remainder[0].strip():
            remainder.pop(0)
        return chart, remainder

    def split_trailing_explanation(language: str, body: list[str]) -> tuple[list[str], list[str]]:
        """Keep prose following a renderer block outside the code fence."""
        shell_languages = {"bash", "shell", "powershell", "ps1"}
        for offset, line in enumerate(body):
            if offset and language in shell_languages and not line.strip():
                return body[:offset], body[offset + 1:]
            if offset and language in shell_languages and re.search(r"[\u0600-\u06ff]", line):
                return body[:offset], body[offset:]
        return body, []

    lines = answer.splitlines()
    mermaid_starts = ("graph ", "flowchart ", "sequenceDiagram", "classDiagram", "stateDiagram",
                      "erDiagram", "gantt", "pie", "journey", "mindmap", "timeline", "xychart-beta")
    cleaned: list[str] = []
    index = 0
    while index < len(lines):
        toolbar = [line.strip().lower() for line in lines[index:index + 5]]
        if toolbar == ["diagram", "code", "copy", "download", "fullscreen"]:
            body = lines[index + 5:]
            if body and body[0].lstrip().startswith(mermaid_starts):
                split_at = next((offset for offset, line in enumerate(body) if not line.strip()), len(body))
                source, remainder = body[:split_at], body[split_at:]
                cleaned.append("```mermaid")
                cleaned.extend(source)
                cleaned.append("```")
                cleaned.extend(remainder)
                break
        labels = [line.strip().lower() for line in lines[index:index + 3]]
        if labels == ["text", "copy", "download"]:
            body_end = index + 3
            while body_end < len(lines):
                candidate = [line.strip().lower() for line in lines[body_end:body_end + 3]]
                if len(candidate) == 3 and candidate[1:] == ["copy", "download"] and candidate[0] in code_languages:
                    break
                body_end += 1
            body = lines[index + 3:body_end]
            chart, remainder = text_chart_body(body)
            if chart:
                cleaned.append("```text")
                cleaned.extend(chart)
                cleaned.append("```")
                cleaned.extend(remainder)
            else:
                cleaned.extend(body)
            index = body_end
            continue
        if len(labels) >= 3 and labels[1:3] == ["copy", "download"] and labels[0] in code_languages:
            next_label = lines[index + 3].strip().lower() if index + 3 < len(lines) else ""
            label_count = 4 if next_label in {"run", "preview"} else 3
            body_end = index + label_count
            while body_end < len(lines):
                candidate = [line.strip().lower() for line in lines[body_end:body_end + 3]]
                if len(candidate) == 3 and candidate[1:] == ["copy", "download"] and candidate[0] in code_languages:
                    break
                body_end += 1
            body = lines[index + label_count:body_end]
            body, explanation = split_trailing_explanation(labels[0], body)
            trailing: list[str] = []
            if labels[0] == "svg":
                svg_text = "\n".join(body)
                closing = svg_text.find("</svg>")
                if closing >= 0:
                    closing += len("</svg>")
                    svg_source, svg_tail = svg_text[:closing], svg_text[closing:]
                    body = svg_source.splitlines()
                    if svg_tail.strip():
                        trailing = [svg_tail.strip()]
            if looks_like_code(labels[0], body):
                cleaned.append(f"```{labels[0]}")
                cleaned.extend(body)
                cleaned.append("```")
                cleaned.extend(trailing)
                cleaned.extend(explanation)
                index = body_end
                continue
        cleaned.append(lines[index])
        index += 1
    return "\n".join(cleaned).strip()


def _normalize_tool_arguments(name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    """Repair one known OpenClaw browser fill shape without broad coercion."""
    if (
        name == "browser"
        and arguments.get("action") == "act"
        and arguments.get("kind") == "fill"
        and "fields" not in arguments
        and isinstance(arguments.get("ref"), str)
        and isinstance(arguments.get("text"), str)
    ):
        normalized = dict(arguments)
        ref = normalized.pop("ref")
        text = normalized.pop("text")
        normalized["fields"] = [{"ref": ref, "text": text}]
        return normalized
    return arguments


def _extract_tool_call(answer: str, tools: list[dict[str, Any]] | None) -> tuple[dict[str, Any] | None, str]:
    if not tools:
        return None, answer
    match = re.search(r"<tool_call>\s*(\{.*\})\s*</tool_call>", answer, re.DOTALL)
    json_match = None
    if not match:
        action_protocol = re.fullmatch(
            r"\s*Action:\s*([A-Za-z_][\w.-]*)\s*\n\s*Action Input:\s*(\{.*\})\s*",
            answer,
            re.DOTALL | re.IGNORECASE,
        )
        if action_protocol:
            try:
                call = {"name": action_protocol.group(1), "arguments": json.loads(action_protocol.group(2))}
            except json.JSONDecodeError:
                return None, answer
            match_start = 0
        else:
            legacy = re.search(r'<invoke\s+name=["\']([^"\']+)["\']>(.*?)</invoke>', answer, re.DOTALL)
        if not action_protocol and not legacy:
            # DeepSeek Web occasionally formats a requested tool call as a
            # Markdown JSON code block instead of emitting our XML-like
            # marker. Accept only an object with the exact tool-call shape;
            # ordinary JSON answers remain visible text.
            json_match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", answer, re.IGNORECASE | re.DOTALL)
            if not json_match:
                # The DeepSeek Web renderer can strip the code-fence and
                # leave labels such as "json / Copy / Download" before the
                # object. Scan JSON objects and accept only the exact shape
                # used for a tool call.
                decoder = json.JSONDecoder()
                for offset in (m.start() for m in re.finditer(r"\{", answer)):
                    try:
                        candidate, _ = decoder.raw_decode(answer[offset:])
                    except json.JSONDecodeError:
                        continue
                    if isinstance(candidate, dict) and "name" in candidate and "arguments" in candidate:
                        call = candidate
                        match_start = offset
                        break
                else:
                    return None, answer
            else:
                try:
                    call = json.loads(json_match.group(1))
                except json.JSONDecodeError:
                    return None, answer
                match_start = json_match.start()
        elif not action_protocol:
            name = legacy.group(1)
            arguments = {
                key: value.strip()
                for key, value in re.findall(r'<parameter\s+name=["\']([^"\']+)["\']>(.*?)</parameter>', legacy.group(2), re.DOTALL)
            }
            call = {"name": name, "arguments": arguments}
            match_start = answer.rfind("<function_calls>", 0, legacy.start())
            if match_start < 0:
                match_start = legacy.start()
    else:
        match_start = match.start()
    try:
        if match and not json_match:
            payload = match.group(1)
            try:
                call = json.loads(payload)
            except json.JSONDecodeError:
                # Some DeepSeek Web responses quote the arguments object but
                # fail to escape its inner quotes, e.g.
                # {"name":"exec","arguments":"{"command":"pwd"}"}.
                # Recover only that explicit tool-call shape; never infer a
                # tool from a plain Bash/code block.
                name_match = re.search(r'"name"\s*:\s*"([^"\\]+)"', payload)
                arguments_pos = re.search(r'"arguments"\s*:\s*', payload)
                if not name_match or not arguments_pos:
                    return None, answer
                object_start = payload.find("{", arguments_pos.end())
                if object_start < 0:
                    return None, answer
                try:
                    call_arguments, _ = json.JSONDecoder().raw_decode(payload[object_start:])
                except json.JSONDecodeError:
                    # The model may also omit escaping quotes inside a
                    # string argument (most often write.content or
                    # exec.command). Recover the known OpenClaw argument
                    # fields conservatively, without executing arbitrary
                    # prose as a command.
                    def string_argument(field: str, end_fields: tuple[str, ...] = ()) -> str | None:
                        field_match = re.search(rf'"{re.escape(field)}"\s*:\s*"', payload[object_start:])
                        if not field_match:
                            return None
                        start = object_start + field_match.end()
                        end = len(payload)
                        for end_field in end_fields:
                            next_field = re.search(rf'"{re.escape(end_field)}"\s*:', payload[start:])
                            if next_field:
                                end = min(end, start + next_field.start() - 1)
                        if end == len(payload):
                            closing = payload.rfind('"}}')
                            end = closing if closing >= start else payload.rfind('"}')
                        if end < start:
                            return None
                        value = payload[start:end]
                        return value.replace(r"\n", "\n").replace(r"\r", "\r").replace(r"\t", "\t").replace(r"\\", "\\")

                    if name_match.group(1) == "write":
                        path = string_argument("path", ("content",))
                        content = string_argument("content")
                        if path is None or content is None:
                            return None, answer
                        call_arguments = {"path": path.rstrip('" ,'), "content": content}
                    elif name_match.group(1) == "exec":
                        command = string_argument("command", ("yieldMs", "timeout"))
                        if command is None:
                            return None, answer
                        call_arguments = {"command": command}
                        for numeric_field in ("yieldMs", "timeout"):
                            numeric_match = re.search(rf'"{numeric_field}"\s*:\s*(\d+)', payload[object_start:])
                            if numeric_match:
                                call_arguments[numeric_field] = int(numeric_match.group(1))
                    else:
                        return None, answer
                call = {"name": name_match.group(1), "arguments": call_arguments}
        name = call.get("name")
        arguments = call.get("arguments", {})
        allowed = {item.get("function", {}).get("name") for item in (tools or [])}
        if not isinstance(name, str) or (allowed and name not in allowed):
            return None, answer
        if isinstance(arguments, str):
            arguments = json.loads(arguments)
        if not isinstance(arguments, dict):
            return None, answer
        arguments = _normalize_tool_arguments(name, arguments)
        return {"id": f"call_{uuid.uuid4().hex}", "type": "function", "function": {
            "name": name, "arguments": json.dumps(arguments, ensure_ascii=False, separators=(",", ":"))
        }}, answer[:match_start].strip()
    except (TypeError, ValueError, json.JSONDecodeError):
        return None, answer
