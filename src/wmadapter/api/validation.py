"""Validate the supported Chat Completions subset before browser side effects."""
import json
from fastapi import HTTPException
from ..providers.contract import ChatRequest, normalize_tool_calls


def validate_input_limit(chat: ChatRequest, max_input_chars: int | None) -> None:
    """Reject oversized input; never truncate content to fit a gateway limit."""
    if max_input_chars is None:
        return
    total = 0
    for message in chat.messages:
        if isinstance(message.content, str):
            total += len(message.content)
        elif isinstance(message.content, list):
            total += len(json.dumps(message.content, ensure_ascii=False, separators=(",", ":")))
    if total > max_input_chars:
        raise HTTPException(400, "Gateway input character limit exceeded")


def validate_chat(chat: ChatRequest, provider, max_input_chars: int | None = None) -> None:
    def invalid(message):
        raise HTTPException(400, message)

    if not chat.messages:
        invalid('messages must not be empty')
    pending = set()
    for message in chat.messages:
        if message.role not in {'system', 'user', 'assistant', 'tool'}:
            invalid('Unsupported message role')
        calls = getattr(message, 'tool_calls', None)
        if calls:
            if message.role != 'assistant' or not isinstance(calls, list):
                invalid('tool_calls must be an assistant array')
            try:
                normalized_calls = normalize_tool_calls(calls)
            except ValueError as exc:
                invalid(str(exc))
            for call in normalized_calls:
                if call['id'] in pending:
                    invalid('Duplicate pending tool call id')
                pending.add(call['id'])
        elif message.role != 'tool' and pending:
            invalid('Tool results must precede the next message')
        if message.role == 'tool':
            call_id = getattr(message, 'tool_call_id', None)
            if not isinstance(call_id, str) or call_id not in pending:
                invalid('tool_call_id must match a pending assistant call')
            pending.remove(call_id)
        content = message.content
        if content is not None and not isinstance(content, (str, list)):
            invalid('content must be text, parts, or null')
        if isinstance(content, list):
            for part in content:
                if not isinstance(part, dict):
                    invalid('Content parts must be objects')
                if part.get('type') == 'text' and isinstance(part.get('text'), str):
                    continue
                if part.get('type') == 'image_url':
                    image = part.get('image_url')
                    url = image.get('url') if isinstance(image, dict) else None
                    if provider.capabilities.image_input and isinstance(url, str) and url.startswith('data:image/'):
                        continue
                    if not provider.capabilities.image_input:
                        invalid('Image input is not currently supported by this provider')
                if part.get('type') in {'video_url', 'input_video', 'video'}:
                    invalid('Unsupported video input')
                if part.get('type') in {'file', 'file_url', 'input_file'}:
                    invalid('Unsupported file or PDF input')
                invalid('Unsupported content part or image URL')
    if pending:
        invalid('Missing tool results for assistant calls')
    names = set()
    for tool in chat.tools or []:
        function = tool.get('function')
        if tool.get('type') != 'function' or not isinstance(function, dict):
            invalid('Only function tools are supported')
        name = function.get('name')
        if not isinstance(name, str) or not name or name in names:
            invalid('Tool names must be nonempty and unique')
        if not isinstance(function.get('parameters', {}), dict):
            invalid('Tool parameters must be a JSON schema object')
        names.add(name)
    choice = chat.tool_choice
    if isinstance(choice, dict):
        function = choice.get('function')
        if choice.get('type') != 'function' or not isinstance(function, dict) or not isinstance(function.get('name'), str) or function.get('name') not in names:
            invalid('tool_choice must name a supplied function')
    elif choice not in (None, 'auto', 'none', 'required'):
        invalid('Unsupported tool_choice')
    if choice == 'required' and not names:
        invalid('required tool_choice needs tools')
    if getattr(chat, 'n', 1) != 1:
        invalid('Only n=1 is supported')
    validate_input_limit(chat, max_input_chars)
