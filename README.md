# 🎵 Music Studio: tạo nhạc theo phong cách riêng

**Tài liệu toàn bộ hệ thống:** [HE_THONG_MUSIC_STUDIO.md](HE_THONG_MUSIC_STUDIO.md) — kiến trúc, chức năng, model, dữ liệu, vận hành và giới hạn hiện tại (đối chiếu mã nguồn ngày 28/10/2026).

**Admin thử nghiệm trên Windows:** bấm đúp **Music Studio.bat**, rồi bấm **Khởi động**.
Cửa sổ điều khiển mở API, worker và giao diện trước; model chỉ được nạp khi chọn DiT/LM rồi bấm **Áp dụng**. Dùng **Dừng tất cả** khi xong.
Xem [hướng dẫn ngắn cho admin](HUONG_DAN.md) để sao lưu và gỡ bỏ.

Hệ thống hỗ trợ tạo nhạc có lời/không lời, Remix và Sửa đoạn; cùng các luồng gán nhãn nhạc tham chiếu, tạo dataset và train LoRA. Xem cách dùng các tác vụ mới trong [hướng dẫn cho admin](HUONG_DAN.md#tạo-mới-remix-và-sửa-đoạn).
- **Máy không GPU:** cả ba loại job chạy `mock`.
- **Máy dev RTX 3060 12GB:** dùng ACE-Step 1.5 `acestep-v15-turbo`, không LM. Pipeline sinh nhạc → gán nhãn → dataset →
  train LoRA → sinh nhạc với LoRA đã chạy được với model thật (kết quả đo bên dưới).
- **Cài cho máy admin, phát hành và cập nhật phiên bản:** xem [DEPLOY.md](DEPLOY.md).

Đổi máy hay đổi backend **chỉ cần đổi file `.env`**.

## Kiến trúc

Hệ thống gồm 4 tiến trình độc lập, giao tiếp với nhau qua HTTP:

```
┌──────────────┐  HTTP   ┌──────────────────────────┐  HTTP (poll)  ┌──────────────┐        ┌──────────────────────────────┐
│ UI Gradio    │ ──────► │ API FastAPI :8000        │ ◄──────────── │ Worker       │ ─────► │ Backend theo loại job        │
│ :7860        │         │ SQLite (jobs = hàng đợi) │  claim/upload │ (1 job/lần)  │        │ generate / autolabel / train │
└──────────────┘         │ data/storage/*           │               └──────────────┘        │  = mock │ ace                │
                         └──────────────────────────┘                                       │          └─► ACE-Step :8001  │
                                                                                            └──────────────────────────────┘
```

| Thành phần | Vai trò | Được phép truy cập |
|---|---|---|
| **UI** (`ui/`) | Giao diện tiếng Việt, kiểu Suno | Chỉ API của chúng ta. Không bao giờ gọi model trực tiếp |
| **API** (`api/`) | Nguồn sự thật duy nhất: DB, file, hàng đợi job; migration tự chạy khi khởi động | SQLite và `data/storage` |
| **Worker** (`worker/`) | Nhận job, chạy backend, upload kết quả, **mỗi lần đúng một job** | Gọi API qua HTTP; đọc thư mục dataset dùng chung trên cùng máy khi train. Không truy cập DB |
| **Backend** (`worker/backends/`) | `MockBackend` hoặc `AceStepBackend`, **chọn riêng cho từng loại job** | `ace`: REST API của ACE-Step |

Vòng đời một job: `queued` → (worker `claim`) → `running` → `done` / `failed`.

Dataset mới được lưu dạng thư mục trong `data/storage/datasets`, không đóng ZIP trong luồng train trên một máy. ZIP chỉ được tạo tạm khi bấm tải xuống; dataset ZIP cũ vẫn được hỗ trợ. Sau khi tạo dataset bằng giao diện, ứng dụng tự dọn các bản tải lên đã được lưu thành công. Có thể xóa bài tải lên và phiên bản dataset trong trang Gán nhãn; các thao tác xóa bị chặn khi có job sử dụng dữ liệu. Xem [hướng dẫn quản lý dữ liệu](HUONG_DAN.md).
- Việc nhận job dùng transaction `BEGIN IMMEDIATE`, nên nhiều worker chạy cùng lúc không bao giờ nhận trùng một job.
- Worker chết giữa chừng (im lặng quá `JOB_STALE_SECONDS`) thì job của nó về lại `queued`.
- Worker khởi động lại với cùng `WORKER_ID` thì job được trả lại hàng đợi ngay. Ctrl+C thì worker tự trả job đang chạy.
- Worker chạy tuần tự, nên sinh nhạc và train **không bao giờ chạy song song** trên cùng một GPU.

## Cài đặt và chạy

Cần **Python 3.11**. Dự án dùng virtualenv riêng `.venv`, không dùng chung với ACE-Step.

```bat
:: Windows                                   # Linux / macOS
scripts\setup.bat                            scripts/setup.sh
scripts\start_all.bat                        scripts/start_all.sh
```

- `setup` tạo `.venv`, cài requirements, và copy `.env.dev` thành `.env` nếu chưa có.
- `start_all` chạy API, worker và UI; Ctrl+C tắt cả ba. Nếu có backend `ace` mà ACE-Step không phản hồi `/health`, nó in cảnh báo
  (vẫn chạy tiếp; job `ace` sẽ báo "ACE-Step chưa bật").
- UI: http://127.0.0.1:7860. Tài liệu API: http://127.0.0.1:8000/docs.
- Chạy riêng từng tiến trình: `scripts/start_api`, `scripts/start_worker`, `scripts/start_ui` (`.sh`/`.bat`).
- Thư viện tùy chọn (`librosa` để ước lượng BPM, `anthropic` cho Claude refiner): `WITH_OPTIONAL=1 scripts/setup.sh`.
  Không cài được thì tính năng tương ứng tự tắt.

### Chọn backend theo loại job (mock ↔ ace)

Đặt trong `.env`:

```bash
GENERATE_BACKEND=ace      # sinh nhạc: mock | ace
AUTOLABEL_BACKEND=mock    # gợi ý nhãn: mock | ace (ace hiện chỉ đo duration/BPM cục bộ)
TRAIN_BACKEND=ace         # train LoRA: mock | ace
```

- Biến cũ `BACKEND=...` vẫn được hiểu, làm giá trị mặc định cho cả ba.
- Chạy hoàn toàn không cần GPU: đặt cả ba là `mock` (hoặc chỉ `BACKEND=mock`).
- Đổi xong thì khởi động lại worker. Tab **Hệ thống** hiện backend đang dùng cho từng loại job và các cảnh báo
  (ví dụ "ACE-Step chưa bật").

Khi có loại job nào dùng `ace`, cần ACE-Step 1.5 chạy ở cùng máy với worker:

```bat
cd D:\ACE-Step-1.5
uv run acestep-api          :: CHỈ API server (cổng 8001). Không chạy Gradio UI của ACE-Step cùng lúc.
```

`D:\ACE-Step-1.5\.env` (cấu hình nhẹ nhất cho 3060):
`ACESTEP_CONFIG_PATH=acestep-v15-turbo`, `ACESTEP_INIT_LLM=false`, `ACESTEP_NO_INIT=false`, không đặt `ACESTEP_CONFIG_PATH2/3`.
Phía Music Studio: `ACESTEP_URL=http://127.0.0.1:8001`, và `ACESTEP_WORK_DIR=D:/ACE-Step-1.5/music_studio_work`
(bắt buộc nằm trong thư mục ACE-Step, vì ACE-Step từ chối đường dẫn train ở ngoài).

Danh sách model DiT/LM **không viết cứng**: worker đọc từ `GET /v1/model_inventory` của ACE-Step, chỉ lấy model đã nạp.
LM chỉ có khi ACE-Step đã nạp LLM. Trên 3060, dropdown chỉ có `acestep-v15-turbo` và LM "Không dùng".

### Chạy worker ở terminal hoặc máy khác

```bash
API_URL=http://192.168.1.10:8000 WORKER_ID=gpu-1 scripts/start_worker.sh
```

Máy chạy API phải đặt `API_HOST=0.0.0.0` và nên đặt `API_TOKEN` (khi đó mọi request phải gửi header `X-Token`).
Worker có backend `ace` phải chạy **cùng máy** với ACE-Step. Chi tiết ở [DEPLOY.md](DEPLOY.md).

### Dọn dữ liệu thử

```bash
scripts/clean_mock_data.sh --dry-run    # Windows: scripts\clean_mock_data.bat --dry-run
scripts/clean_mock_data.sh              # in danh sách rồi hỏi 'yes' mới xóa
```

Script xoá bản ghi và file có `source='mock'` (track, LoRA) hoặc `backend='mock'` (bài sinh ra), **xoá theo dây chuyền**:
dataset chứa track mock, LoRA train từ dataset đó, bài dùng LoRA đó. Lịch sử job được giữ lại.
Track upload mới luôn là `source='ace'`, tức nhạc tham chiếu thật, nên không bao giờ bị xoá nhầm.

## Cách dùng UI

Giao diện nền tối dành cho máy tính, có thanh điều hướng dọc bên trái để chuyển giữa các chức năng. Trang Tạo nhạc có biểu mẫu bên trái và danh sách bài dạng thẻ bên phải,
thanh phát nhạc cố định ở đáy. Theme/CSS nằm trong `ui/theme.py`.

1. **✦ Tạo nhạc:**
   - Một biểu mẫu: tên bài tùy chọn và mô tả phong cách nhạc (caption tiếng Anh). Bỏ trống tên thì hệ thống tự đặt.
   - Tag phong cách bấm chọn được. "✨ Kiểm tra prompt" hiện bản gốc và bản đề xuất để bạn chọn.
   - Thanh **Model** có lựa chọn DiT/LM đã tải và nút **Áp dụng và khởi động lại engine**. Đổi lựa chọn chưa tác động đến engine; Áp dụng chỉ được chấp nhận khi không có job. API/UI vẫn mở trong lúc engine nạp lại. Bên dưới là model/LoRA đang dùng, seed và độ mạnh LoRA **0–1**; LoRA được lọc theo DiT thực tế đang chạy. Cấu hình áp dụng qua UI chỉ có hiệu lực trong phiên, không sửa `.env`.
   - Thời lượng và số bản mỗi lần tạo hiển thị trực tiếp. Viết BPM, tông và nhịp trong mô tả; giao diện không gửi thông số riêng để ghi đè. Seed nằm trong khung chọn model (-1 = ngẫu nhiên).
   - Mỗi thẻ bài hiện backend, model và seed, kèm nút ▶, 👍/👎 và ↺ Dùng lại (nạp lại toàn bộ cài đặt và seed).
   - Banner cảnh báo khi không có worker hoặc ACE-Step chưa bật.
2. **♫ Thư viện:** lọc theo đánh giá, tìm kiếm; bấm ⓘ để xem backend, model, LoRA, seed và tham số.
3. **🏷 Gán nhãn:** upload, gợi ý nhãn, sửa caption/tags/BPM/key, Duyệt hoặc Loại. "Tạo phiên bản dataset" đóng gói
   **mọi track đã duyệt**; mọi track trong đó phải có caption, vì ACE-Step chỉ train mẫu có caption.
4. **🏋 Finetune:** chọn dataset, base model, phong cách, rank, learning rate, epochs. Có tiến độ và log loss.
   LoRA được nhóm theo phong cách.
5. **⚙ Hệ thống:** backend theo loại job, worker, model, hàng đợi, cảnh báo.

Mỗi lần sinh nhạc lưu đủ `backend`, `dit_model`, `lm_model`, `lora_id`, `lora_scale`, `seed` thật và các tham số,
để tái lập kết quả và so sánh A/B.

## Kết quả đo trên RTX 3060 12GB (máy dev)

Cấu hình: ACE-Step 1.5 commit `ca1e85f`, `acestep-v15-turbo` (2B), không LM, API server `quantization=None`, CPU offload tự bật
(ACE-Step xếp 12GB vào tier4), PyTorch 2.7.1+cu128, driver 591.86. Đo ngày 23/10/2026.
VRAM đo bằng `nvidia-smi`, đã gồm khoảng 0.5–0.65GB của desktop.

| Hạng mục | Kết quả |
|---|---|
| Cài môi trường ACE-Step (`uv sync`) | 424 giây; `.venv` 6.1GB (+ cache uv khoảng 6GB) |
| Model tải về lần đầu (`checkpoints/`) | **9.40GB**: turbo 4.46GB, Qwen3-Embedding 1.12GB, VAE 0.31GB, LM 1.7B 3.50GB (gói chính luôn kèm LM dù không dùng) |
| Khởi động `acestep-api` khi đã có model | khoảng 13 giây |
| VRAM lúc rảnh (đã nạp turbo) | **5.2–5.7GB** |
| VRAM đỉnh khi sinh một bài 30 giây | **6.8GB** |
| Thời gian sinh một bài 30 giây (`inference_steps=8`, `batch_size=1`) | **khoảng 6.5 giây** trong ACE-Step; 8–12 giây tính từ lúc bấm tới khi có file |
| File đầu ra | WAV 48kHz stereo float32, 30 giây khoảng 11MB |
| Cùng seed / khác seed | Cùng bài (sai khác mẫu bằng 0) / bài khác hẳn (tương quan 0.002) |
| Tắt rồi bật lại ACE-Step | UI báo "ACE-Step chưa bật…", job fail sau khoảng 4 giây; bật lại thì job kế tiếp chạy bình thường |
| Tiền xử lý dataset (3 bài × 30 giây) | khoảng 8 giây (VRAM xuống 2.8GB, decoder tạm đưa sang CPU) |
| **Train LoRA** (3 bài, rank 8, alpha 16, lr 1e-4, 2 epoch = 6 bước, batch 1, gradient checkpointing) | khoảng 14 giây; **đỉnh 9.1GB** (train lần đầu sau khi khởi động ACE-Step); loss cuối 1.36 |
| Cả job train (scan + tiền xử lý + train + `/v1/init`) | khoảng 35 giây |
| Sinh nhạc có LoRA | Chạy được; lịch sử ghi đúng `lora_id`/`lora_scale`. Cùng seed: LoRA x1.0 khác bài gốc (tương quan 0.997) và tái lập được; tắt LoRA thì ra **đúng** bài gốc. |

Khác biệt giữa server thật và tài liệu, đã xử lý trong adapter:
- `GET /v1/models` bị adapter OpenRouter chiếm (định dạng OpenAI `{"object":"list"}`). Danh sách đúng nằm ở `GET /v1/model_inventory`.
- Kết quả trả `lm_model` = LM mặc định trong cấu hình kể cả khi không dùng LM. Adapter chỉ ghi LM khi `thinking=true`.
- ACE-Step xuất WAV float32 mà module `wave` của Python không đọc được. `shared/audio.py` đọc header RIFF để lấy thời lượng.

## Cách ACE-Step xử lý LoRA

Xác minh bằng **mã nguồn** ACE-Step 1.5 (commit `ca1e85f`) và bằng chạy thật trên 3060. `API.md` không liệt kê hết các endpoint dưới đây.

### (a) Gắn LoRA khi sinh nhạc (`acestep/api/http/lora_routes.py`, `core/generation/handler/lora/`)

| Endpoint | Body | Ghi chú |
|---|---|---|
| `POST /v1/lora/load` | `{lora_path, adapter_name?}` | Đường dẫn **trên máy chạy ACE-Step**: thư mục PEFT có `adapter_config.json` (hoặc file LoKr). Nạp được nhiều adapter, mỗi adapter một tên. |
| `POST /v1/lora/scale` | `{scale, adapter_name?}` | Chỉ nhận **0.0–1.0**; đồng thời chọn adapter đang dùng. Bị bỏ qua nếu LoRA đang tắt. |
| `POST /v1/lora/toggle` | `{use_lora}` | PEFT `enable/disable_adapter_layers`. Tắt thì cho **đúng** output của model gốc (đã kiểm chứng). |
| `POST /v1/lora/unload` | — | ⚠️ **Hỏng ở commit này**, xem "Vấn đề còn tồn tại". Adapter không dùng. |
| `GET /v1/lora/status` | — | `lora_loaded, use_lora, adapters, scales, active_adapter` |

- LoRA là **trạng thái chung của model đang nạp**, không theo từng request. Worker xử lý như sau:
  - Mỗi LoRA nạp **một lần** dưới tên `ms_lora_<id>`, với file adapter tải về `ACESTEP_WORK_DIR/loras/` (cache).
  - Job có LoRA: `toggle(true)` rồi `scale(scale, adapter_name)`.
  - Job không có LoRA: `toggle(false)`.
- Không nạp được LoRA trên model đã lượng tử hoá (INT8). API server mặc định không lượng tử hoá.
- LoRA do backend `mock` tạo (file giả) không gắn được vào ACE-Step: job báo lỗi rõ ràng.

### (b) Dataset và tiền xử lý thành tensor (`acestep/api/train_api_dataset_*`, `training/dataset_builder_modules/`)

1. `POST /v1/dataset/scan {audio_dir, dataset_name, all_instrumental=true}`: dataset nằm trong bộ nhớ server. Mỗi bài gồm:
   - một file audio;
   - `<stem>.caption.txt`: **có caption thì mẫu mới được train**;
   - `<stem>.json` với `{caption, bpm, keyscale, timesignature}`.

   Nhạc không lời: **không** tạo `<stem>.lyrics.txt` và **không** ghi `language` vào JSON, vì cả hai làm ACE-Step coi bài là có lời.
   Zip dataset của chúng ta đã xuất đúng định dạng này, kèm `manifest.json` chứa metadata đầy đủ.
2. `POST /v1/dataset/preprocess_async {output_dir}`, sau đó poll `GET /v1/dataset/preprocess_status/{task_id}` để lấy file `.pt` kèm `manifest.json`.
3. Mọi đường dẫn train phải nằm **trong thư mục làm việc của tiến trình ACE-Step** (`training/path_safety.py`).

### (c) Train, theo dõi, dừng, file đầu ra (`train_api_lora_start_route.py`, `train_api_service.py`)

- `POST /v1/training/start {tensor_dir, lora_rank, lora_alpha, lora_dropout, learning_rate, train_epochs, train_batch_size,
  gradient_accumulation, save_every_n_epochs, training_seed, lora_output_dir, gradient_checkpointing, use_fp8}`: chạy nền, trả về ngay.
  Worker gửi `train_batch_size=1`, `gradient_accumulation=1` (dataset rất nhỏ), `gradient_checkpointing=true`,
  `lora_alpha = 2×rank`, và chỉ lưu bản final.
- Theo dõi: `GET /v1/training/status` trả `is_training, current_epoch, current_step, current_loss, status, loss_history, error`.
  Dừng: `POST /v1/training/stop`.
- Đầu ra: `<lora_output_dir>/final/adapter/` (`adapter_config.json` + `adapter_model.safetensors`, định dạng PEFT).
  Worker upload thư mục này lên API (`data/storage/loras/lora_jobNNNNNN/`, `source='ace'`).
- **Train dùng chính model sinh nhạc đang nạp** (chèn LoRA vào decoder tại chỗ) và để lại LoRA trong decoder sau khi xong.
  Vì vậy worker gọi `POST /v1/init` sau train, và cả trước train nếu đang có LoRA nạp sẵn, để có decoder sạch.

**Quy trình job train (backend `ace`):**
1. Kiểm tra `base_model` đúng là model đang nạp.
2. Giải nén zip vào `ACESTEP_WORK_DIR`.
3. Nếu đang có LoRA thì `/v1/init`.
4. `scan` → `preprocess_async` (poll) → `training/start` (poll tiến độ và loss).
5. Upload adapter lên API.
6. `/v1/init`, rồi dọn thư mục làm việc.

Nếu không đọc được trạng thái train (xem vấn đề 2 bên dưới), worker gọi `stop` và **chờ xác nhận đã dừng** rồi mới nạp lại model hay xoá tensor.

## Vấn đề còn tồn tại

1. **VRAM không về lại mức rảnh sau train (lỗi phía ACE-Step).** `training_state["trainer"]` vẫn giữ tham chiếu tới model
   đã train. `/v1/init` nạp model mới nhưng model cũ không được giải phóng. Đo trên 3060:
   - lúc rảnh 5.2GB → sau train và `/v1/init` còn **9.6–10.0GB**;
   - **train lần 2 đạt đỉnh 11.99GB / 12GB**, vẫn chạy xong nhưng sát giới hạn;
   - sinh nhạc lúc đó vẫn được (đỉnh 11.2GB).

   Cách chắc chắn là **khởi động lại `acestep-api` sau mỗi lần train** (mất khoảng 13 giây). Hiện phải làm tay; worker chưa tự quản lý tiến trình ACE-Step.
2. **Loss NaN làm `/v1/training/status` trả HTTP 500** (ACE-Step không chuyển NaN sang JSON được). Gặp khi dataset có bài
   rất ngắn hoặc lạ (3 track sine 2–4 giây, 8kHz của giai đoạn mock). Worker phát hiện, dừng train, báo lỗi rõ ràng.
   Không gặp với bài thật dài 30 giây.
3. **`/v1/lora/unload` không gỡ được LoRA (lỗi ACE-Step).** `get_base_model()` vẫn còn các lớp LoRA chèn trong module, và
   `load_state_dict(strict=False)` lệch khoá (`q_proj.weight` so với `q_proj.base_layer.weight`) nên không khôi phục gì.
   Hậu quả nếu dùng: bài "không LoRA" vẫn chạy với LoRA. Adapter đã chuyển sang dùng `toggle` (đã kiểm chứng ra đúng bài gốc) và không gọi unload.
4. **Autolabel bằng ACE-Step:** có `/v1/dataset/auto_label` nhưng cần LM, mà máy 3060 không nạp LM. `AUTOLABEL_BACKEND=ace` hiện chỉ đo duration/BPM cục bộ.
5. **Worker `ace` phải chạy cùng máy với ACE-Step**, vì nó truyền đường dẫn file cục bộ. Hai worker không được dùng chung một ACE-Step.
6. "Tạo phiên bản dataset" trên UI gom **mọi** track đã duyệt. Muốn loại track nào thì đánh dấu Loại trước (API hỗ trợ `track_ids`).
7. Chưa chạy thử các script `.sh` trên Linux/macOS (máy dev là Windows).

## Prompt refiner (Claude, tùy chọn)

- Mặc định `PROMPT_REFINER=noop`: trả nguyên văn.
- `PROMPT_REFINER=claude` cùng `ANTHROPIC_API_KEY`, `CLAUDE_MODEL` (mặc định `claude-opus-5-5`), cần cài `anthropic`.
  Refiner chuyển mô tả (kể cả tiếng Việt) thành caption tiếng Anh có cấu trúc, cảnh báo mâu thuẫn (ví dụ "nhạc thiền" với BPM 170),
  và dùng caption đã duyệt trong DB làm ví dụ văn phong. Bật `fallbacks: "default"`: nếu model từ chối, Anthropic tự chạy lại trên model dự phòng.
- Thiếu key, lỗi mạng hoặc model từ chối thì refiner **tự lùi về NoOp** và trả warning.

## Cấu trúc thư mục

```
api/            main.py, db.py (SQLite + hàng đợi), migrations.py (schema_version), storage.py, cleanup.py, routes/
worker/         worker.py (poll, 1 job/lần), api_client.py, backends/{base, mock, music_synth, acestep}.py
ui/             app.py (Gradio), theme.py (CSS kiểu Suno), client.py
shared/         schemas.py, config.py (.env), audio.py (WAV PCM/float, librosa tùy chọn)
prompt_refiner/ base.py, noop.py, claude.py
scripts/        setup, start_all, start_api, start_worker, start_ui, clean_mock_data (.sh/.bat), run_all.py
data/           app.db + storage/{uploads,outputs,loras,datasets} (gitignore)
tests/          pytest
```

## Test

```bash
.venv/bin/python -m pytest -q          # Windows: .venv\Scripts\python.exe -m pytest -q
```

Bộ test không cần GPU hay ACE-Step thật:
- **hàng đợi:** claim đồng thời 8 luồng × 60 job không trùng, requeue khi worker chết hoặc khởi động lại, dọn worker cũ;
- **chọn backend theo loại job:** biến mới và biến `BACKEND` cũ, định tuyến job, mỗi lần một job, báo cáo lên `/models` và `/system`;
- **migration:** DB giai đoạn 1 được nâng cấp không mất dữ liệu, `seed` được điền từ `params`, chạy lại không đổi gì;
- **`clean_mock_data`:** xoá theo dây chuyền, giữ dữ liệu thật;
- **MockBackend:** WAV đúng thời lượng, tái lập theo seed, không tràn bộ đệm;
- **AceStepBackend với HTTP giả lập (respx):**
  - sinh nhạc, seed, tiến độ, ACE-Step tắt rồi bật lại;
  - LoRA load/toggle/scale (không bao giờ unload);
  - train đầy đủ, NaN → 500 → dừng trước khi nạp lại, không xác nhận được dừng thì không đụng model;
- **refiner:** các trường hợp fallback;
- **luồng đầu cuối qua HTTP:** upload, gán nhãn, dataset (định dạng ACE-Step), train, lọc LoRA, path traversal, token.
