"""Compatibility adapter for existing complete(prompt)->str providers."""
from .contract import ProviderRequest, ProviderResult
from .protocol import _prompt, _image_attachments, _resolve_web_answer
from .normalizer import ToolProtocolNormalizer


async def infer_legacy(provider, request: ProviderRequest) -> ProviderResult:
    chat = request.chat
    tools = chat.tools if chat.tool_choice != "none" else None
    if isinstance(chat.tool_choice, dict):
        name = chat.tool_choice["function"]["name"]
        tools = [tool for tool in tools or [] if tool["function"]["name"] == name]
    prompt = _prompt(chat.messages, request.system_prompt, tools)
    required = chat.tool_choice == "required" or isinstance(chat.tool_choice, dict)
    if required:
        prompt += "\nTOOL CHOICE: You must return one of the listed tool calls, not a final text answer."
    images = _image_attachments(chat.messages)
    if images:
        if not provider.capabilities.image_input:
            raise ValueError("This provider does not support image input")
        answer = await provider.complete_with_attachments(
            prompt, conversation_id=request.conversation_id, attachments=images
        )
    else:
        answer = await provider.complete(prompt, conversation_id=request.conversation_id)
    call, visible = await _resolve_web_answer(
        provider, answer, chat.messages, tools, request.conversation_id, prompt
    )
    if call is None:
        call, visible = ToolProtocolNormalizer().normalize(visible, tools)
    if required and call is None:
        raise ValueError("Web model did not honor required tool_choice")
    return ProviderResult(content=None if call else visible,
                          tool_calls=[call] if call else [],
                          finish_reason="tool_calls" if call else "stop")
