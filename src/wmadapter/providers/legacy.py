"""Compatibility adapter for existing complete(prompt)->str providers."""
import hashlib
import logging

from .contract import ProviderRequest, ProviderResult
from .errors import ContextLimitError
from .protocol import _prompt, _image_attachments, _resolve_web_answer, compact_messages, minimize_tool_schemas
from .normalizer import ToolProtocolNormalizer
from .recovery import ToolCallRecovery

logger = logging.getLogger(__name__)


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
    tools = minimize_tool_schemas(tools, compact_descriptions=request.context_budget_chars is not None)
    required = choice == "required" or isinstance(choice, dict)

    def build_prompt(current_messages):
        value = (adapter.prompt(current_messages, request.system_prompt, tools, request.client_policy)
                 if adapter else _prompt(current_messages, request.system_prompt, tools, request.client_policy))
        if required:
            value += "\nTOOL CHOICE: You must return one of the listed tool calls, not a final text answer."
        return value

    prompt = build_prompt(messages)
    budget = request.context_budget_chars
    if budget is None:
        budget = provider.context_budget_for(chat.model) if hasattr(provider, "context_budget_for") else None
    compacted = False
    if budget is not None and len(prompt) > budget:
        try:
            messages = compact_messages(messages, max(1024, budget // 2))
        except ContextLimitError as exc:
            raise ContextLimitError(len(prompt), budget) from exc
        prompt = build_prompt(messages)
        compacted = True
        if len(prompt) > budget:
            raise ContextLimitError(len(prompt), budget)
    logger.info(
        "provider_request_metrics provider=%s model=%s message_count=%d tool_count=%d prompt_length=%d prompt_sha256=%s compacted=%s client_max_tokens=%s",
        provider.name, chat.model, len(messages), len(tools or []), len(prompt),
        hashlib.sha256(prompt.encode("utf-8", "replace")).hexdigest(), compacted,
        request.client_max_tokens,
    )
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
                           if adapter else ToolCallRecovery().resolve(
                               provider, answer, messages, tools, request.conversation_id, prompt
                           ))
    if call is None:
        call, visible = ToolProtocolNormalizer().normalize(visible, tools)
    if required and call is None:
        raise ValueError("Web model did not honor required tool_choice")
    return ProviderResult(content=None if call else visible,
                          tool_calls=[call] if call else [],
                          finish_reason="tool_calls" if call else "stop")
