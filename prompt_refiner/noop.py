from __future__ import annotations

from typing import Any

from .base import PromptRefiner, RefineResult


class NoOpRefiner(PromptRefiner):
    """Mặc định: trả nguyên văn."""
    name = "noop"

    def refine(self, text: str, context: dict[str, Any]) -> RefineResult:
        return {"refined": text.strip(), "warnings": [], "suggested_params": {}}
