from __future__ import annotations

from shared.config import Settings

from .base import PromptRefiner, RefineResult
from .noop import NoOpRefiner


def get_refiner(settings: Settings) -> PromptRefiner:
    if settings.prompt_refiner == "claude":
        from .claude import ClaudeRefiner
        return ClaudeRefiner(settings.anthropic_api_key, settings.claude_model)
    return NoOpRefiner()


__all__ = ["PromptRefiner", "RefineResult", "NoOpRefiner", "get_refiner"]
