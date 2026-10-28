"""Cấu hình đọc từ file .env (python-dotenv).

Thứ tự ưu tiên: biến môi trường đã có > file chỉ định bởi ENV_FILE > .env ở thư mục gốc.
Đổi máy (dev -> GPU) chỉ cần đổi file .env.
"""
from __future__ import annotations

import os
import socket
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

ROOT_DIR = Path(__file__).resolve().parent.parent
_file_env_values: dict[str, str] = {}


def _load_env_file() -> None:
    env_file = os.getenv("ENV_FILE")
    path = Path(env_file) if env_file else ROOT_DIR / ".env"
    if not path.is_absolute():
        path = ROOT_DIR / path
    if path.exists():
        existing = set(os.environ)
        load_dotenv(path, override=False)
        _file_env_values.update({key: os.environ[key] for key in set(os.environ) - existing})


def _str(name: str, default: str = "") -> str:
    return os.getenv(name, default).strip()


def _int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except ValueError:
        return default


def _float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except ValueError:
        return default


def _list(name: str, default: str = "") -> list[str]:
    return [x.strip() for x in os.getenv(name, default).split(",") if x.strip()]


@dataclass
class Settings:
    # --- API ---
    api_host: str = "127.0.0.1"
    api_port: int = 8000
    api_url: str = "http://127.0.0.1:8000"
    api_token: str = ""
    data_dir: Path = ROOT_DIR / "data"
    job_stale_seconds: int = 60  # worker im lặng lâu hơn mức này -> job của nó về lại queued
    worker_online_seconds: int = 30

    # --- UI ---
    ui_host: str = "127.0.0.1"
    ui_port: int = 7860

    # --- Worker: backend theo từng loại job (mock | ace) ---
    generate_backend: str = "mock"
    autolabel_backend: str = "mock"
    train_backend: str = "mock"
    worker_id: str = ""
    worker_types: list[str] = field(default_factory=lambda: ["generate", "autolabel", "train"])
    worker_poll_interval: float = 2.0

    # --- Model mặc định (chỉ là gợi ý; danh sách thật do worker báo cáo) ---
    default_dit_model: str = ""
    default_lm_model: str = ""

    # --- MockBackend ---
    mock_dit_models: list[str] = field(default_factory=list)
    mock_lm_models: list[str] = field(default_factory=list)
    mock_delay_min: float = 2.0
    mock_delay_max: float = 5.0
    mock_train_seconds: float = 25.0

    # --- AceStepBackend ---
    acestep_url: str = "http://127.0.0.1:8001"
    acestep_api_key: str = ""
    acestep_lm_models: list[str] = field(default_factory=list)
    acestep_poll_interval: float = 2.0
    acestep_timeout: float = 1800.0
    acestep_inference_steps: int = 0  # 0 = để server tự chọn mặc định
    # Thư mục làm việc cho train/LoRA, PHẢI nằm trong thư mục làm việc của tiến trình ACE-Step
    # (ACE-Step từ chối đường dẫn ngoài thư mục đó). Worker phải chạy cùng máy với ACE-Step.
    acestep_work_dir: str = ""
    acestep_train_timeout: float = 3600.0
    acestep_train_grad_checkpointing: bool = True
    essentia_enabled: bool = False
    essentia_executable: str = ""
    essentia_timeout: float = 180.0

    # --- Cập nhật online: URL tới latest.json (trống = dùng update_url trong release.json) ---
    update_url: str = ""

    # --- Prompt refiner ---
    prompt_refiner: str = "noop"
    anthropic_api_key: str = ""
    claude_model: str = ""

    @property
    def backends(self) -> dict[str, str]:
        """{loại job: backend}, ví dụ {"generate": "ace", "autolabel": "mock", "train": "mock"}."""
        return {"generate": self.generate_backend, "autolabel": self.autolabel_backend,
                "train": self.train_backend}

    @property
    def storage_dir(self) -> Path:
        return self.data_dir / "storage"

    @property
    def db_path(self) -> Path:
        return self.data_dir / "app.db"

    @classmethod
    def from_env(cls) -> "Settings":
        _load_env_file()
        data_dir = Path(_str("DATA_DIR", "data"))
        if not data_dir.is_absolute():
            data_dir = ROOT_DIR / data_dir
        api_port = _int("API_PORT", 8000)
        # Tương thích ngược: BACKEND cũ là giá trị mặc định cho cả ba loại job.
        legacy_backend = _str("BACKEND", "mock").lower() or "mock"
        return cls(
            api_host=_str("API_HOST", "127.0.0.1"),
            api_port=api_port,
            api_url=_str("API_URL", f"http://127.0.0.1:{api_port}").rstrip("/"),
            api_token=_str("API_TOKEN"),
            data_dir=data_dir,
            job_stale_seconds=_int("JOB_STALE_SECONDS", 60),
            worker_online_seconds=_int("WORKER_ONLINE_SECONDS", 30),
            ui_host=_str("UI_HOST", "127.0.0.1"),
            ui_port=_int("UI_PORT", 7860),
            generate_backend=(_str("GENERATE_BACKEND") or legacy_backend).lower(),
            autolabel_backend=(_str("AUTOLABEL_BACKEND") or legacy_backend).lower(),
            train_backend=(_str("TRAIN_BACKEND") or legacy_backend).lower(),
            worker_id=_str("WORKER_ID") or f"{socket.gethostname()}-{os.getpid()}",
            worker_types=_list("WORKER_TYPES", "generate,autolabel,train"),
            worker_poll_interval=_float("WORKER_POLL_INTERVAL", 2.0),
            default_dit_model=_str("DEFAULT_DIT_MODEL"),
            default_lm_model=_str("DEFAULT_LM_MODEL"),
            mock_dit_models=_list("MOCK_DIT_MODELS", "acestep-v15-turbo"),
            mock_lm_models=_list("MOCK_LM_MODELS", "none,acestep-5Hz-lm-0.6B"),
            mock_delay_min=_float("MOCK_DELAY_MIN", 2.0),
            mock_delay_max=_float("MOCK_DELAY_MAX", 5.0),
            mock_train_seconds=_float("MOCK_TRAIN_SECONDS", 25.0),
            acestep_url=_str("ACESTEP_URL", "http://127.0.0.1:8001").rstrip("/"),
            acestep_api_key=_str("ACESTEP_API_KEY"),
            acestep_lm_models=_list("ACESTEP_LM_MODELS", "none,acestep-5Hz-lm-1.7B"),
            acestep_poll_interval=_float("ACESTEP_POLL_INTERVAL", 2.0),
            acestep_timeout=_float("ACESTEP_TIMEOUT", 1800.0),
            acestep_inference_steps=_int("ACESTEP_INFERENCE_STEPS", 0),
            acestep_work_dir=_str("ACESTEP_WORK_DIR"),
            acestep_train_timeout=_float("ACESTEP_TRAIN_TIMEOUT", 3600.0),
            essentia_enabled=_str("ESSENTIA_ENABLED", "true").lower() in ("1", "true", "yes"),
            essentia_executable=_str("ESSENTIA_EXECUTABLE"),
            essentia_timeout=_float("ESSENTIA_TIMEOUT", 180.0),
            acestep_train_grad_checkpointing=_str("ACESTEP_TRAIN_GRAD_CHECKPOINTING", "true").lower()
            in ("1", "true", "yes"),
            update_url=_str("UPDATE_URL"),
            prompt_refiner=_str("PROMPT_REFINER", "noop").lower(),
            anthropic_api_key=_str("ANTHROPIC_API_KEY"),
            claude_model=_str("CLAUDE_MODEL"),
        )


_settings: Settings | None = None


def get_settings() -> Settings:
    global _settings
    if _settings is None:
        _settings = Settings.from_env()
    return _settings


def reset_settings() -> None:
    """Dùng trong test sau khi đổi biến môi trường."""
    global _settings
    _settings = None


def reload_settings() -> Settings:
    """Reload dotenv values while preserving explicit environment overrides."""
    for key, value in _file_env_values.items():
        if os.environ.get(key) == value:
            os.environ.pop(key, None)
    _file_env_values.clear()
    reset_settings()
    return get_settings()
