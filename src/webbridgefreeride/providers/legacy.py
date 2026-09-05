"""Compatibility adapter for existing complete(prompt)->str providers."""
from .contract import ProviderRequest, ProviderResult
from .protocol import _prompt, _image_attachments, _resolve_web_answer
from .normalizer import ToolProtocolNormalizer


async def infer_legacy(provider, request: ProviderRequest) -> ProviderResult:
    chat = request.chat
    canonical = request.canonical
    messages = canonical.messages if canonical is not None else chat.messages
    raw_tools = ([tool.model_dump() for tool in canonical.tools] if canonical is not None else chat.tools) or []
    choice = canonical.tool_choice if canonical is not None else chat.tool_choice
    tools = raw_tools if choice != "none" else None
    if isinstance(choice, dict):
        name = choice["function"]["name"]
        tools = [tool for tool in tools or [] if tool["function"]["name"] == name]
    adapter = getattr(provider, "protocol", None)
    prompt = (adapter.prompt(messages, request.system_prompt, tools, request.client_policy)
              if adapter else _prompt(messages, request.system_prompt, tools, request.client_policy))
    required = choice == "required" or isinstance(choice, dict)
    if required:
        prompt += "\nTOOL CHOICE: You must return one of the listed tool calls, not a final text answer."
    images = adapter.attachments(messages) if adapter else _image_attachments(messages)
    if images:
        if not provider.capabilities.image_input:
            raise ValueError("This provider does not support image input")
        answer = await provider.complete_with_attachments(
            prompt, conversation_id=request.conversation_id, attachments=images
        )
    else:
        answer = await provider.complete(prompt, conversation_id=request.conversation_id)
    call, visible = await (adapter.resolve(provider, answer, messages, tools, request.conversation_id, prompt)
                           if adapter else _resolve_web_answer(provider, answer, messages, tools, request.conversation_id, prompt))
    if call is None:
        call, visible = ToolProtocolNormalizer().normalize(visible, tools)
    if required and call is None:
        raise ValueError("Web model did not honor required tool_choice")
    return ProviderResult(content=None if call else visible,
                          tool_calls=[call] if call else [],
                          finish_reason="tool_calls" if call else "stop")
