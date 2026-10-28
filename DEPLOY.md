# Triển khai và cập nhật trên máy admin

Mô hình: **một máy Windows có GPU NVIDIA**, chạy cả Music Studio lẫn ACE-Step. Bạn (người phát triển) đóng gói
mỗi bản thành một file ZIP; admin cài lần đầu một lần, các lần sau chỉ chạy file cập nhật.

| Thành phần | Ai quản lý | Cập nhật bằng |
|---|---|---|
| Code Music Studio | Gói ZIP, đăng lên kênh online | Nút **Cập nhật** trong cửa sổ điều khiển, hoặc `Cap nhat Music Studio.bat` |
| Thư viện Python của Music Studio | `requirements.lock` trong gói | Tự cài khi lock đổi |
| Database (`app.db`) | Migration trong code | Tự chạy khi cập nhật, chỉ thêm cột/bảng |
| `.env`, audio, dataset, LoRA (`data/`) | Máy admin | Không bao giờ bị gói ghi đè |
| ACE-Step và model DiT/LM | Cài riêng | Thủ công (mục 5), cửa sổ Quản lý model |

## 1. Cài lần đầu trên máy admin

Cần: Windows 10/11, GPU NVIDIA và driver mới, **Python 3.11**, `uv` (để cài ACE-Step).

**ACE-Step** (một lần):

```powershell
git clone https://github.com/ACE-Step/ACE-Step-1.5.git D:\ACE-Step-1.5
cd D:\ACE-Step-1.5
uv sync
```

Tạo `D:\ACE-Step-1.5\.env` theo VRAM (bảng ở `docs/en/GPU_COMPATIBILITY.md` của ACE-Step), ví dụ:

```dotenv
ACESTEP_CONFIG_PATH=acestep-v15-turbo
ACESTEP_INIT_LLM=true
ACESTEP_LM_MODEL_PATH=acestep-5Hz-lm-1.7B
```

Không bật lượng tử hoá (INT8): ACE-Step không nạp hay train được LoRA trên model đã lượng tử hoá.
Model có thể tải sau trong cửa sổ Music Studio → **Quản lý model DiT / LM**.

**Music Studio**:

1. Giải nén `MusicStudio-<phiên bản>.zip`, đổi tên thư mục bên trong thành `D:\ai_music_studio`.
2. Chạy `D:\ai_music_studio\scripts\setup.bat`: tạo `.venv`, cài thư viện đúng phiên bản trong `requirements.lock`,
   tải Essentia, tạo `.env` từ `.env.dev`.
3. Sửa `D:\ai_music_studio\.env` cho máy này. Tối thiểu kiểm tra:

   ```dotenv
   GENERATE_BACKEND=ace
   AUTOLABEL_BACKEND=ace
   TRAIN_BACKEND=ace
   WORKER_ID=admin-worker
   ACESTEP_URL=http://127.0.0.1:8001
   ACESTEP_WORK_DIR=D:/ACE-Step-1.5/music_studio_work
   MUSIC_STUDIO_ENGINE_DIR=D:/ACE-Step-1.5
   ```

   `ACESTEP_WORK_DIR` bắt buộc nằm trong thư mục ACE-Step. Nếu bên ACE-Step đặt `ACESTEP_API_KEY` thì điền cùng giá trị.
4. Bấm đúp `Music Studio.bat` → **Khởi động** → vào **Tạo nhạc → Model**, chọn DiT/LM, bấm **Áp dụng**.

Muốn mang dữ liệu cũ sang (bài đã tạo, dataset, LoRA): trên máy cũ bấm **Sao lưu dữ liệu**, trên máy admin giải nén
`data/app.db` và `data/storage/` của bản sao lưu vào `D:\ai_music_studio\data\` **trước** lần khởi động đầu.

## 2. Cập nhật phiên bản mới (admin)

**Cách thường dùng: online.** Khi bạn đăng bản mới (mục 7), cửa sổ điều khiển trên mỗi máy tự phát hiện
(lúc mở và mỗi 6 giờ, hoặc bấm **Kiểm tra cập nhật**) và hiện dòng "Có bản mới …" kèm nút **Cập nhật**.
Giao diện web cũng hiện thông báo ở thanh bên. Admin chờ tác vụ đang chạy xong rồi bấm **Cập nhật**: ứng dụng
dừng dịch vụ, tải gói, kiểm tra SHA-256, đóng cửa sổ điều khiển, chạy trình cập nhật trong một cửa sổ console
(các bước trong bảng dưới), rồi tự mở lại cửa sổ điều khiển. Lỗi thì tự quay về bản đang dùng.

**Cách thủ công** (máy không có mạng, hoặc chưa cấu hình kênh online):

1. Chờ tác vụ đang chạy xong, bấm **Dừng tất cả**, **đóng** cửa sổ điều khiển.
2. Giải nén `MusicStudio-<bản mới>.zip` ra một thư mục **riêng** (ví dụ `Downloads`), không giải nén đè lên `D:\ai_music_studio`.
3. Trong thư mục vừa giải nén, bấm đúp `Cap nhat Music Studio.bat`, xác nhận thư mục cài (mặc định `D:\ai_music_studio`).
4. Đọc kết quả. Nếu có dòng "Biến cấu hình mới…", xem `CHANGELOG.md` để biết có cần thêm vào `.env` không.
5. Mở lại `Music Studio.bat`. Phiên bản hiện ở thanh bên giao diện và tiêu đề cửa sổ điều khiển.

Trình cập nhật làm lần lượt:

| Bước | Việc làm |
|---|---|
| Kiểm tra | Từ chối nếu cửa sổ điều khiển còn mở hoặc cổng API/UI/ACE-Step còn bị chiếm |
| Sao lưu | Code hiện tại + bản chụp `app.db` vào `D:\ai_music_studio\backups\<thời điểm>_v<bản cũ>\` (giữ 5 bản gần nhất) |
| Thay code | Thay toàn bộ `api`, `worker`, `ui`, `shared`, `scripts`, `prompt_refiner`, `tests` và các file gốc của gói |
| Thư viện | Chỉ chạy `pip install` khi `requirements.lock` khác lần trước (cần mạng) |
| Kiểm tra | Chạy migration database và thử khởi tạo API, worker, giao diện |

Lỗi ở bất kỳ bước nào sau khi thay code thì **tự khôi phục** code, thư viện và `app.db` về như trước, máy vẫn dùng
được bản cũ. Lịch sử cập nhật ghi ở `data\logs\update.log`.

Không bị đụng tới: `.env`, `.venv` (chỉ cài thêm/đổi phiên bản thư viện), `data\` (audio, dataset, LoRA, log),
model và cài đặt ACE-Step, extractor Essentia đã tải.

## 3. Quay lại bản trước (admin)

Nếu bản mới chạy được nhưng có lỗi khi dùng: dừng tất cả, đóng cửa sổ điều khiển, bấm đúp
`D:\ai_music_studio\Khoi phuc ban truoc.bat`.

Mặc định chỉ trả **code và thư viện** về bản trước; database giữ nguyên nên bài, nhãn, dataset tạo sau khi cập nhật
không mất. Điều này an toàn vì migration chỉ thêm cột/bảng, code cũ vẫn đọc được database mới.
Chỉ khi database có vấn đề mới trả cả database về lúc sao lưu (mất dữ liệu tạo sau thời điểm đó):

```powershell
cd D:\ai_music_studio
.venv\Scripts\python.exe scripts\update.py --target . --rollback --with-db
```

## 4. Phát hành bản mới (người phát triển)

1. Sửa code, chạy `.venv\Scripts\python.exe -m pytest -q`.
2. Tăng số trong `VERSION`:
   - `1.0.0 → 1.0.1`: sửa lỗi, không đổi cách dùng.
   - `1.0.x → 1.1.0`: thêm tính năng, thêm biến `.env`, thêm migration.
   - `1.x → 2.0.0`: thay đổi cần admin làm thêm thao tác thủ công.
3. Thêm mục `## <phiên bản>` vào đầu `CHANGELOG.md`: có gì mới, có biến `.env` nào mới (kèm giá trị gợi ý), có cần làm gì thêm.
4. Nếu đã đổi thư viện (`pip install …` trong `.venv`), thêm `--freeze` để ghi lại `requirements.lock`.
5. Đóng gói:

   ```powershell
   .venv\Scripts\python.exe scripts\make_release.py            # chạy test rồi tạo dist\MusicStudio-<phiên bản>.zip
   .venv\Scripts\python.exe scripts\make_release.py --freeze   # khi đã đổi thư viện
   ```

6. Đăng bản lên kênh online (mục 7). Nếu chưa dùng kênh online thì gửi file ZIP cho admin.

Quy tắc giữ cho cập nhật an toàn:

- **Migration chỉ thêm**: thêm hàm `_vN` mới trong `api/migrations.py`, dùng `ALTER TABLE ADD COLUMN`/`CREATE … IF NOT EXISTS`,
  không xóa hay đổi tên cột, không sửa migration đã phát hành. Đây là điều kiện để "Khôi phục bản trước" không mất dữ liệu.
- **Biến `.env` mới phải có mặc định** trong `shared/config.py`, để máy admin chưa thêm biến vẫn chạy như cũ. Thêm biến đó vào `.env.dev`
  để trình cập nhật nhắc admin.
- **Không đổi cấu trúc thư mục `data/`** mà không có bước chuyển đổi tự động trong migration hoặc khi khởi động.
- Thêm thư mục code mới ở gốc dự án: khai báo trong `scripts/release_layout.py` (`CODE_DIRS`), nếu không sẽ không có trong gói.
- Mỗi bản đã gửi đi giữ lại file ZIP trong `dist\` để có thể gửi lại khi cần.

## 5. Cập nhật ACE-Step (riêng, thủ công)

ACE-Step không nằm trong gói Music Studio. Chỉ cập nhật khi cần (sửa lỗi engine, model mới), vì Music Studio gọi
các endpoint đã đối chiếu với một phiên bản ACE-Step cụ thể (`worker/backends/acestep.py`).

1. Dừng tất cả, đóng cửa sổ điều khiển.
2. `cd D:\ACE-Step-1.5`, `git pull`, `uv sync`. Muốn quay lại: `git checkout <commit cũ>`, `uv sync`.
3. Khởi động Music Studio, thử một lần: tạo nhạc, gán nhãn AI và một lần train ngắn trước khi dùng thật.

Nên thử bản ACE-Step mới trên máy phát triển trước, rồi ghi commit đã thử vào `CHANGELOG.md` cho admin.

## 7. Kênh cập nhật online

Mỗi máy hỏi một địa chỉ **cố định** `latest.json` để biết bản mới nhất. File này do `make_release.py` tạo,
gồm số phiên bản, link tải ZIP, SHA-256 của ZIP và ghi chú lấy từ `CHANGELOG.md`. Máy chỉ cài gói khớp SHA-256,
và chỉ chấp nhận địa chỉ `https://`.

### Thiết lập một lần

Chọn nơi lưu trữ có link HTTPS cố định, rồi tạo `release.config.json` ở thư mục gốc dự án **trên máy bạn**
(file này không vào gói). Địa chỉ `feed_url` được ghi vào mọi gói đóng sau đó, nên các máy cài từ gói đó
tự biết hỏi ở đâu, không cần sửa `.env`.

**GitHub Releases**: xem hướng dẫn từng bước ở mục 7.1. `release.config.json` chỉ cần:

```json
{ "github_repo": "<tài-khoản>/music-studio-releases" }
```

**Nơi lưu trữ riêng** (Cloudflare R2/S3 bucket public, web server của bạn), giữ được repo private:

```json
{
  "feed_url": "https://updates.ten-mien-cua-ban.com/music-studio/latest.json",
  "download_url": "https://updates.ten-mien-cua-ban.com/music-studio/MusicStudio-{version}.zip"
}
```

Máy đã cài từ gói cũ chưa có `feed_url` (hoặc muốn đổi địa chỉ): thêm `UPDATE_URL=<feed_url>` vào `.env` của máy
đó một lần. `UPDATE_URL` trong `.env` luôn được ưu tiên hơn địa chỉ ghi trong gói.

### Mỗi lần phát hành

```powershell
.venv\Scripts\python.exe scripts\make_release.py      # tạo dist\MusicStudio-<ver>.zip và dist\latest.json
```

Với GitHub Releases thêm `--publish` để đăng luôn (mục 7.1). Với nơi lưu trữ riêng: tải `MusicStudio-<ver>.zip` lên
trước, rồi ghi đè `latest.json` (các máy chỉ thấy bản mới khi latest.json đổi). Tắt cache dài cho `latest.json`
(ví dụ `Cache-Control: no-cache`) để các máy thấy bản mới ngay.

Rút một bản lỗi: đăng lại `latest.json` của bản tốt trước đó (hoặc phát hành ngay bản sửa với số cao hơn).
Máy đã lên bản lỗi dùng `Khoi phuc ban truoc.bat`. Máy chưa cập nhật sẽ không thấy bản lỗi nữa.

### 7.1. Đăng bản lên GitHub Releases

Các máy tải `https://github.com/<tài-khoản>/<repo>/releases/latest/download/latest.json`. GitHub luôn trỏ
`releases/latest` tới release mới nhất, nên địa chỉ này cố định và mỗi release chứa cả ZIP lẫn `latest.json`
(đăng trong một lần, không có lúc lệch nhau).

Máy admin tải không cần đăng nhập, nên repo chứa release phải **public**: ai có link cũng tải được gói, gồm mã nguồn
Python. Nên dùng một repo **riêng chỉ để phát hành** (ví dụ `music-studio-releases`, chỉ có README). Mã nguồn để
ở repo private hoặc chỉ trên máy bạn: repo phát hành không cần chứa code, release chỉ là file đính kèm.

**Thiết lập một lần (máy phát triển):**

1. Đăng nhập GitHub, bật **xác thực hai lớp** (Settings → Password and authentication).
2. Tạo repo phát hành: **New repository** → tên `music-studio-releases` → **Public** →
   tích **Add a README file** (release cần repo có ít nhất một commit) → **Create repository**.
3. Cài GitHub CLI và đăng nhập (PowerShell):

   ```powershell
   winget install --id GitHub.cli
   # đóng và mở lại terminal
   gh auth login        # GitHub.com -> HTTPS -> Login with a web browser
   ```

4. Tạo `D:\ai_music_studio\release.config.json` (chỉ ở máy bạn, không vào gói):

   ```json
   { "github_repo": "<tài-khoản>/music-studio-releases" }
   ```

5. Đăng bản đầu tiên:

   ```powershell
   cd D:\ai_music_studio
   .venv\Scripts\python.exe scripts\make_release.py --force --publish
   ```

   `--force` chỉ cần lần này vì `dist\MusicStudio-1.0.0.zip` cũ được đóng khi chưa có địa chỉ cập nhật.
   Script chạy test, đóng gói (ghi sẵn địa chỉ `latest.json` vào gói), tạo release `v1.0.0` có ZIP + `latest.json`
   + ghi chú từ CHANGELOG, rồi đọc lại link `latest.json` để xác nhận.
6. Kiểm tra: mở `https://github.com/<tài-khoản>/music-studio-releases/releases` thấy **Music Studio 1.0.0** (Latest).
7. Cài máy admin từ chính file ZIP trên trang release đó (mục 1). Từ đây máy admin tự nhận các bản sau.

**Mỗi lần phát hành:**

```powershell
# 1. sửa code, tăng VERSION (vd 1.0.1), thêm "## 1.0.1" vào CHANGELOG.md
.venv\Scripts\python.exe scripts\make_release.py --publish
```

Trong vòng 6 giờ (hoặc ngay khi admin mở lại ứng dụng / bấm **Kiểm tra cập nhật**) mọi máy hiện "Có bản mới 1.0.1".

**Không dùng GitHub CLI:** chạy `make_release.py` không có `--publish`, rồi trên web: repo → **Releases** →
**Draft a new release** → **Choose a tag**: gõ `v1.0.1` → **Create new tag** → tiêu đề `Music Studio 1.0.1` →
kéo thả **cả hai** file `dist\MusicStudio-1.0.1.zip` và `dist\latest.json` → để tích **Set as the latest release** →
**Publish release**. Tên tag phải đúng `v<VERSION>` vì link tải trong `latest.json` dùng tên này.

**Rút bản lỗi trên GitHub:** đặt release tốt trước đó làm Latest (`gh release edit v1.0.0 --repo <tài-khoản>/music-studio-releases --latest`,
hoặc trên web: Edit release cũ → tích **Set as the latest release**), rồi xóa hoặc chuyển bản lỗi thành pre-release.
Các máy chưa cập nhật sẽ không thấy bản lỗi nữa.

### Bảo mật

Ai sửa được file trên nơi lưu trữ là có thể đưa code xuống mọi máy (SHA-256 nằm trong chính latest.json).
Bật xác thực hai lớp cho tài khoản GitHub/cloud, chỉ cấp quyền ghi cho người phát hành, không chia sẻ token.

## 8. Nhiều máy (tham khảo)

Mặc định mọi thứ chạy trên một máy. Có thể tách API/UI và worker sang máy khác (`API_HOST=0.0.0.0`, đặt `API_TOKEN`),
nhưng worker có backend `ace` phải chạy **cùng máy** với ACE-Step, và train dataset dạng thư mục đọc trực tiếp
`data/storage`, nên worker train phải cùng máy với API. Hai worker không được dùng chung một ACE-Step.
