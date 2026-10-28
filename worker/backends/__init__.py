from __future__ import annotations

from shared.config import Settings

from .base import Backend, BackendResult, JobContext

BACKEND_NAMES = ("mock", "ace")


def make_backend(name: str, settings: Settings) -> Backend:
    if name == "mock":
        from .mock import MockBackend
        return MockBackend(settings)
    if name == "ace":
        from .acestep import AceStepBackend
        return AceStepBackend(settings)
    raise ValueError(f"Backend không hợp lệ: '{name}' (chỉ hỗ trợ {' | '.join(BACKEND_NAMES)})")


def make_backends(settings: Settings) -> dict[str, Backend]:
    """{loại job: backend} theo GENERATE_BACKEND / AUTOLABEL_BACKEND / TRAIN_BACKEND.
    Các loại job dùng chung tên backend thì dùng chung một instance."""
    instances: dict[str, Backend] = {}
    result = {}
    for job_type, name in settings.backends.items():
        if name not in instances:
            instances[name] = make_backend(name, settings)
        result[job_type] = instances[name]
    return result


__all__ = ["Backend", "BackendResult", "JobContext", "BACKEND_NAMES", "make_backend", "make_backends"]
