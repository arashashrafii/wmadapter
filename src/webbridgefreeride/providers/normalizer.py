from __future__ import annotations

from typing import Any

from .protocol import _clean_renderer_artifacts, _extract_tool_call


class ToolProtocolNormalizer:
    """Convert a WebChat's textual answer into the shared provider result shape."""

    def normalize(self, answer: str, tools: list[dict[str, Any]] | None) -> tuple[dict[str, Any] | None, str]:
        call, visible = _extract_tool_call(answer, tools)
        if call is None:
            visible = _clean_renderer_artifacts(visible)
        return call, visible
