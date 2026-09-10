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


def validate_chat(chat: ChatRequest, provider, max_input_chars: int | None = None,
                  *, allow_max_tokens: bool = False) -> None:
    def invalid(message):
        raise HTTPException(400, message)

    if not chat.messages:
        invalid('messages must not be empty')
    if chat.max_tokens is not None and chat.max_completion_tokens is not None:
        invalid('Specify only one of max_tokens or max_completion_tokens')
    for field in (
        'temperature', 'top_p', 'max_tokens', 'max_completion_tokens',
        'presence_penalty', 'frequency_penalty', 'seed', 'stop',
    ):
        if field == 'max_tokens' and allow_max_tokens:
            continue
        if getattr(chat, field, None) is not None:
            invalid(f'Unsupported sampling control: {field}')
    if chat.n != 1:
        invalid('Only n=1 is supported')
    if chat.parallel_tool_calls:
        invalid('parallel_tool_calls=true is not supported by the web adapter')
    if isinstance(chat.stop, list) and (not chat.stop or not all(isinstance(item, str) and item for item in chat.stop)):
        invalid('stop must be a non-empty string or array of non-empty strings')
    if chat.stream_options:
        unsupported = sorted(set(chat.stream_options) - {'include_usage'})
        if unsupported:
            invalid(f'Unsupported stream_options field: {unsupported[0]}')
        if not chat.stream:
            invalid('stream_options requires stream=true')
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
        if not isinstance(tool, dict):
            invalid('Tool definitions must be objects')
        if tool.get('type') == 'custom':
            invalid('Custom tools are not supported by the web adapter')
        function = tool.get('function')
        if tool.get('type') != 'function' or not isinstance(function, dict):
            invalid('Only function tools are supported')
        unsupported = sorted(set(function) - {'name', 'description', 'parameters', 'strict'})
        if unsupported:
            invalid(f'Unsupported function tool field: {unsupported[0]}')
        name = function.get('name')
        if not isinstance(name, str) or not name or len(name) > 64 or name in names:
            invalid('Tool names must be nonempty, at most 64 characters, and unique')
        if not all(character.isalnum() or character in {'_', '-'} for character in name):
            invalid('Tool names may contain only letters, numbers, underscores, and hyphens')
        if 'description' in function and not isinstance(function['description'], str):
            invalid('Tool description must be a string')
        if not isinstance(function.get('parameters', {}), dict):
            invalid('Tool parameters must be a JSON schema object')
        if 'strict' in function and not isinstance(function['strict'], bool):
            invalid('Tool strict must be boolean')
        names.add(name)
    choice = chat.tool_choice
    if isinstance(choice, dict):
        function = choice.get('function')
        if (set(choice) != {'type', 'function'} or choice.get('type') != 'function'
                or not isinstance(function, dict) or set(function) != {'name'}
                or not isinstance(function.get('name'), str) or function.get('name') not in names):
            invalid('tool_choice must name a supplied function')
    elif choice not in (None, 'auto', 'none', 'required'):
        invalid('Unsupported tool_choice')
    if choice == 'required' and not names:
        invalid('required tool_choice needs tools')
    validate_input_limit(chat, max_input_chars)
