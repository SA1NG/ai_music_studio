from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, TypedDict


class RefineResult(TypedDict):
    refined: str
    warnings: list[str]
    suggested_params: dict[str, Any]


class PromptRefiner(ABC):
    name: str = "base"

    @abstractmethod
    def refine(self, text: str, context: dict[str, Any]) -> RefineResult:
        """context có thể chứa: bpm, key_scale, duration, approved_captions (list[str])."""
