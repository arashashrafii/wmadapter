from __future__ import annotations

from typing import Any

from .protocol import ToolProtocolStatus, _clean_renderer_artifacts, _extract_tool_call_status


class ToolProtocolNormalizer:
    """Convert a WebChat's textual answer into the shared provider result shape."""

    def normalize(self, answer: str, tools: list[dict[str, Any]] | None) -> tuple[dict[str, Any] | None, str]:
        _, call, visible = self.normalize_with_status(answer, tools)
        return call, visible

    def normalize_with_status(self, answer: str, tools: list[dict[str, Any]] | None) -> tuple[ToolProtocolStatus, dict[str, Any] | None, str]:
        status, call, visible = _extract_tool_call_status(answer, tools)
        if call is None:
            visible = _clean_renderer_artifacts(visible)
        return status, call, visible
