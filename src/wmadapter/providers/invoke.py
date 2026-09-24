from __future__ import annotations

import inspect


async def invoke_provider(
    provider,
    method_name: str,
    prompt: str,
    *,
    conversation_id: str | None = None,
    model: str | None = None,
    **kwargs,
):
    """Invoke a provider method while preserving legacy provider fakes."""
    method = getattr(provider, method_name)
    parameters = inspect.signature(method).parameters
    if model is not None and ("model" in parameters or any(
        parameter.kind is inspect.Parameter.VAR_KEYWORD
        for parameter in parameters.values()
    )):
        kwargs["model"] = model
    if conversation_id is not None or "conversation_id" in parameters:
        kwargs["conversation_id"] = conversation_id
    try:
        return await method(prompt, **kwargs)
    except TypeError as exc:
        # AsyncMock and a few legacy adapters expose only (*args, **kwargs)
        # in their signature while their wrapped callable rejects new kwargs.
        if model is not None and "unexpected keyword argument 'model'" in str(exc):
            kwargs.pop("model", None)
            return await method(prompt, **kwargs)
        raise
