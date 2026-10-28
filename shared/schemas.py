"""Pydantic schemas dùng chung giữa API, worker và UI."""
from __future__ import annotations

from typing import Any, Literal, Optional

from pydantic import BaseModel, Field, model_validator

JobType = Literal["generate", "autolabel", "train"]
JobStatus = Literal["queued", "running", "done", "failed"]
ReviewStatus = Literal["pending", "approved", "rejected"]

NO_LM = "none"  # giá trị LM nghĩa là "không dùng LM" (thinking=false)


# ---------- Model ----------
class ModelInfo(BaseModel):
    name: str
    is_default: bool = False
    description: str = ""


class ModelList(BaseModel):
    dit_models: list[ModelInfo] = []
    lm_models: list[ModelInfo] = []
    backends: list[str] = Field([], description="Backend sinh nhạc của các worker (nguồn của danh sách model)")
    backends_by_type: dict[str, list[str]] = {}
    workers_online: int = 0
    warnings: list[str] = Field([], description="Cảnh báo từ worker online, ví dụ 'ACE-Step chưa bật'")


# ---------- Worker ----------
BackendName = Literal["mock", "ace"]


class WorkerRegister(BaseModel):
    worker_id: str
    backend: str = Field(description="Tóm tắt, ví dụ 'generate=ace,autolabel=mock,train=mock'")
    backends: dict[JobType, BackendName] = Field({}, description="Backend theo từng loại job")
    dit_models: list[ModelInfo] = []
    lm_models: list[ModelInfo] = []
    capabilities: list[JobType] = []
    info: dict[str, Any] = Field({}, description="Thêm: warnings (list[str]) — vấn đề của backend, hiện trên UI")
    fresh_start: bool = Field(True, description="True = worker vừa khởi động (job nó đang giữ bị trả về hàng đợi); "
                                                "False = chỉ cập nhật trạng thái định kỳ")


class WorkerInfo(WorkerRegister):
    last_seen: str
    online: bool = False


class ClaimRequest(BaseModel):
    worker_id: str
    types: list[JobType] = ["generate", "autolabel", "train"]


class ProgressUpdate(BaseModel):
    progress: float = Field(ge=0.0, le=1.0)
    log: Optional[str] = None  # dòng log nối thêm (không ghi đè)


class FailRequest(BaseModel):
    error: str


# ---------- Job ----------
class Job(BaseModel):
    id: int
    type: JobType
    status: JobStatus
    payload: dict[str, Any] = {}
    result: Optional[dict[str, Any]] = None
    progress: float = 0.0
    log: str = ""
    error: Optional[str] = None
    worker_id: Optional[str] = None
    created_at: str
    updated_at: str


class JobCreated(BaseModel):
    job_id: int


# ---------- Generate ----------
class GenerateRequest(BaseModel):
    prompt: str = Field(min_length=1, description="Caption cuối cùng gửi cho model")
    prompt_original: Optional[str] = Field(None, description="Mô tả gốc của người dùng (nếu đã refine)")
    title: str = Field("", max_length=120, description="Tên bài (bỏ trống = tự đặt từ mô tả)")
    dit_model: str
    lm_model: str = NO_LM
    lora_id: Optional[int] = None
    lora_scale: float = Field(1.0, ge=0.0, le=1.0, description="ACE-Step nhận 0–1")
    duration: float = Field(30.0, ge=10.0, le=600.0, description="ACE-Step nhận 10–600 giây")
    bpm: Optional[int] = Field(None, ge=30, le=300)
    key_scale: str = ""
    time_signature: str = ""
    seed: int = Field(-1, description="-1 = ngẫu nhiên (seed thật được ghi lại trong kết quả)")
    inference_steps: Optional[int] = None
    audio_format: Literal["wav", "mp3", "flac"] = "wav"
    task_type: Literal["text2music", "cover", "repaint"] = "text2music"
    instrumental: bool = True
    lyrics: str = Field("", max_length=12000)
    vocal_language: str = "unknown"
    source_audio: Optional[str] = None
    reference_audio: Optional[str] = None
    audio_cover_strength: float = Field(0.8, ge=0, le=1)
    repainting_start: float = Field(0, ge=0)
    repainting_end: Optional[float] = Field(None, gt=0)
    repaint_mode: Literal["conservative", "balanced", "aggressive"] = "balanced"
    thinking: bool = True
    use_cot_caption: bool = False
    guidance_scale: float = Field(7, ge=1, le=15)
    lm_temperature: float = Field(0.85, ge=0, le=2)
    lm_negative_prompt: str = Field("", max_length=2000)

    @model_validator(mode="after")
    def validate_generation_options(self):
        if self.inference_steps is not None and not 1 <= self.inference_steps <= 200:
            raise ValueError("Số bước sinh phải nằm trong khoảng 1–200")
        if "turbo" in self.dit_model.lower() and (self.inference_steps or 8) > 20:
            raise ValueError("Turbo chỉ nhận tối đa 20 bước; để 0 trên giao diện để dùng mặc định")
        if not self.instrumental and not self.lyrics.strip():
            raise ValueError("Hãy nhập lời hát khi chọn Có lời")
        if self.task_type != "text2music" and not self.source_audio:
            raise ValueError("Remix và Sửa đoạn cần audio nguồn")
        if self.task_type == "repaint" and (
            self.repainting_end is None or self.repainting_end <= self.repainting_start
        ):
            raise ValueError("Điểm kết thúc phải lớn hơn điểm bắt đầu")
        return self


class Generation(BaseModel):
    id: int
    job_id: int
    prompt_original: str
    prompt_final: str
    dit_model: str
    lm_model: str
    lora_id: Optional[int] = None
    lora_scale: float = 1.0
    params: dict[str, Any] = {}
    audio_path: str
    rating: int = 0
    backend: str = "mock"
    seed: Optional[int] = None
    created_at: str


class RatingRequest(BaseModel):
    rating: Literal[-1, 0, 1]


# ---------- Prompt refine ----------
class RefineRequest(BaseModel):
    text: str
    context: dict[str, Any] = {}


class RefineResponse(BaseModel):
    original: str
    refined: str
    warnings: list[str] = []
    suggested_params: dict[str, Any] = {}
    refiner: str = "noop"


# ---------- Tracks ----------
class Track(BaseModel):
    id: int
    filename: str
    path: str
    duration: Optional[float] = None
    bpm: Optional[float] = None
    key_scale: str = ""
    time_signature: str = ""
    caption: str = ""
    is_instrumental: bool = True
    lyrics: str = ""
    vocal_language: str = "unknown"
    tags: list[str] = []
    auto_labels: dict[str, Any] = {}
    review_status: ReviewStatus = "pending"
    source: str = "ace"
    created_at: str = ""
    autolabel_job_id: Optional[int] = None


class TrackUpdate(BaseModel):
    is_instrumental: Optional[bool] = None
    lyrics: Optional[str] = Field(None, max_length=12000)
    vocal_language: Optional[str] = Field(None, max_length=32)
    bpm: Optional[float] = None
    key_scale: Optional[str] = None
    time_signature: Optional[str] = None
    caption: Optional[str] = None
    tags: Optional[list[str]] = None
    review_status: Optional[ReviewStatus] = None


class AutoLabelRequest(BaseModel):
    require_ai: bool = False
    is_instrumental: Optional[bool] = None
    lyrics: Optional[str] = Field(None, max_length=12000)
    vocal_language: Optional[str] = Field(None, max_length=32)
    transcribe_lyrics: bool = True


# ---------- Datasets ----------
class DatasetCreate(BaseModel):
    name: str = Field("", max_length=120)
    track_ids: Optional[list[int]] = Field(None, description="Bỏ trống = mọi track đã approved")
    base_dataset_id: Optional[int] = Field(None, ge=1)
    cleanup_sources: bool = False

    @model_validator(mode="after")
    def require_dataset_name(self):
        if self.base_dataset_id is None and not self.name.strip():
            raise ValueError("Nhập tên cho dataset mới")
        return self


class Dataset(BaseModel):
    id: int
    name: str
    version: int
    track_ids: list[int]
    export_path: str
    created_at: str
    removed_track_ids: list[int] = []
    cleanup_warnings: list[str] = []


# ---------- Train / LoRA ----------
class TrainParams(BaseModel):
    rank: int = Field(16, ge=1, le=256)
    learning_rate: float = Field(1e-4, gt=0)
    epochs: int = Field(10, ge=1, le=10000)


class TrainRequest(BaseModel):
    dataset_id: int
    base_model: str = Field(min_length=1, description="Model gốc dùng để train (bắt buộc)")
    style_name: str = Field(min_length=1)
    name: Optional[str] = None
    params: TrainParams = TrainParams()


class Lora(BaseModel):
    id: int
    name: str
    style_name: str
    base_model: str
    dataset_id: Optional[int] = None
    params: dict[str, Any] = {}
    path: str
    source: str = "mock"
    created_at: str
