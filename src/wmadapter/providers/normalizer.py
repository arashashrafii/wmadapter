from __future__ import annotations

from typing import Any

from .protocol import ToolProtocolStatus, _clean_renderer_artifacts, _extract_tool_call_status
import re


def _normalize_tabular_markdown(text: str) -> str:
    """Convert renderer tab-separated tables to portable Markdown tables."""
    lines = text.splitlines()
    out: list[str] = []
    in_fence = False
    index = 0
    while index < len(lines):
        line = lines[index]
        if line.lstrip().startswith("```"):
            in_fence = not in_fence
            out.append(line)
            index += 1
            continue
        if not in_fence and "\t" in line:
            block: list[str] = []
            while index < len(lines) and "\t" in lines[index] and not lines[index].lstrip().startswith("```"):
                block.append(lines[index])
                index += 1
            if len(block) >= 2:
                rows = [[cell.strip() for cell in row.split("\t")] for row in block]
                width = max(map(len, rows))
                rows = [row + [""] * (width - len(row)) for row in rows]
                out.append("| " + " | ".join(rows[0]) + " |")
                out.append("| " + " | ".join("---" for _ in range(width)) + " |")
                out.extend("| " + " | ".join(row) + " |" for row in rows[1:])
                continue
            out.extend(block)
            continue
        out.append(line)
        index += 1
    return "\n".join(out)


class ToolProtocolNormalizer:
    """Convert a WebChat's textual answer into the shared provider result shape."""

    def normalize(self, answer: str, tools: list[dict[str, Any]] | None) -> tuple[dict[str, Any] | None, str]:
        _, call, visible = self.normalize_with_status(answer, tools)
        return call, visible

    def normalize_with_status(self, answer: str, tools: list[dict[str, Any]] | None) -> tuple[ToolProtocolStatus, dict[str, Any] | None, str]:
        status, call, visible = _extract_tool_call_status(answer, tools)
        if call is None:
            visible = _clean_renderer_artifacts(visible)
            visible = _normalize_tabular_markdown(visible)
        return status, call, visible
