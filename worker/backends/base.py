"""Interface backend. Backend chỉ biết JobContext (tiến độ, tải file đầu vào, thư mục tạm),
không biết gì về DB hay HTTP của API."""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional, Protocol

from shared.schemas import ModelInfo


class JobContext(Protocol):
    job_id: int
    payload: dict[str, Any]
    workdir: Path

    def progress(self, value: float, log: Optional[str] = None) -> None: ...

    def download(self, rel_path: str) -> Path:
        """Tải file từ storage của API (qua GET /files/...) về workdir."""
        ...


class BackendError(RuntimeError):
    """Lỗi đã có thông báo rõ ràng cho người dùng (worker báo nguyên văn, không kèm traceback)."""


@dataclass
class BackendResult:
    result: dict[str, Any] = field(default_factory=dict)
    files: list[Path] = field(default_factory=list)


class Backend(ABC):
    name: str = "base"
    capabilities: list[str] = ["generate", "autolabel", "train"]

    @abstractmethod
    def list_models(self) -> tuple[list[ModelInfo], list[ModelInfo]]:
        """Trả (dit_models, lm_models)."""

    def list_models_or_last(self) -> tuple[list[ModelInfo], list[ModelInfo]]:
        """Như list_models nhưng không ném lỗi khi dịch vụ phía sau tạm tắt (backend nào cần thì ghi đè)."""
        return self.list_models()

    def status(self) -> Optional[str]:
        """None nếu backend sẵn sàng; ngược lại là thông báo lỗi ngắn để hiện trên UI."""
        return None

    @abstractmethod
    def generate(self, ctx: JobContext) -> BackendResult: ...

    @abstractmethod
    def autolabel(self, ctx: JobContext) -> BackendResult: ...

    @abstractmethod
    def train(self, ctx: JobContext) -> BackendResult: ...

    def run(self, job_type: str, ctx: JobContext) -> BackendResult:
        handler = {"generate": self.generate, "autolabel": self.autolabel, "train": self.train}.get(job_type)
        if handler is None:
            raise ValueError(f"Loại job không hỗ trợ: {job_type}")
        return handler(ctx)
