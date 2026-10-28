# Music Studio — Tài liệu toàn bộ hệ thống

Ngày đối chiếu mã nguồn: **28/10/2026**.

Tài liệu này mô tả ứng dụng hiện có, dành cho admin sử dụng thử và người tiếp tục phát triển. Các cổng và đường dẫn dưới đây là cấu hình mặc định hoặc ví dụ trên máy hiện tại; `.env` có thể thay đổi chúng. Đây không phải báo cáo trạng thái các tiến trình đang chạy.

## Mục lục

1. [Mục đích và phạm vi](#1-mục-đích-và-phạm-vi)
2. [Kiến trúc và trách nhiệm](#2-kiến-trúc-và-trách-nhiệm)
3. [Engine, DiT, LM và LoRA](#3-engine-dit-lm-và-lora)
4. [Khởi động, đổi model và dừng](#4-khởi-động-đổi-model-và-dừng)
5. [Trang Tạo nhạc](#5-trang-tạo-nhạc)
6. [Trang Thư viện](#6-trang-thư-viện)
7. [Gán nhãn và kiểm chứng bằng Essentia](#7-gán-nhãn-và-kiểm-chứng-bằng-essentia)
8. [Dataset và quản lý dung lượng](#8-dataset-và-quản-lý-dung-lượng)
9. [Finetune](#9-finetune)
10. [Theo dõi hệ thống và tài nguyên](#10-theo-dõi-hệ-thống-và-tài-nguyên)
11. [Hàng đợi và phục hồi tác vụ](#11-hàng-đợi-và-phục-hồi-tác-vụ)
12. [Dữ liệu và cấu trúc mã nguồn](#12-dữ-liệu-và-cấu-trúc-mã-nguồn)
13. [Cấu hình và cài đặt](#13-cấu-hình-và-cài-đặt)
14. [API chính](#14-api-chính)
15. [Sao lưu, chuyển máy và cập nhật](#15-sao-lưu-chuyển-máy-và-cập-nhật)
16. [Xử lý lỗi thường gặp](#16-xử-lý-lỗi-thường-gặp)
17. [Giới hạn và hướng phát triển](#17-giới-hạn-và-hướng-phát-triển)
18. [Đối chiếu và kiểm thử](#18-đối-chiếu-và-kiểm-thử)

## 1. Mục đích và phạm vi

Music Studio là ứng dụng thử nghiệm nhạc AI trên **một máy Windows**, giúp admin:

- Tạo nhạc có lời hoặc không lời, remix và sửa một đoạn audio.
- Chọn DiT, LM và adapter LoRA phù hợp.
- Nghe, đánh giá và dùng lại cấu hình bài đã tạo.
- Tải nhạc tham chiếu lên, tạo nhãn tự động, kiểm tra và sửa nhãn.
- Tạo hoặc bổ sung phiên bản dataset; huấn luyện LoRA từ dataset.
- Quản lý model tải về, theo dõi dịch vụ và tài nguyên máy.

Mục tiêu hiện tại là vận hành dễ hiểu cho admin thử nghiệm. Ứng dụng chưa có hệ thống tài khoản, phân quyền nhiều người dùng, bộ cài độc lập hay cập nhật tự động.

## 2. Kiến trúc và trách nhiệm

Có ba lớp sản phẩm: **giao diện → backend → engine/model**. Khi vận hành, backend được chia thành API và worker, nên có bốn dịch vụ chính; ngoài ra có cửa sổ điều khiển desktop.

```text
Music Studio.bat
  └─ Cửa sổ điều khiển desktop (Tkinter)
       ├─ UI Gradio :7860 ──HTTP──► API FastAPI :8000
       │                           ├─ SQLite: dữ liệu + hàng đợi
       │                           └─ Storage: audio, dataset, LoRA
       ├─ Worker ──HTTP poll──────► API
       │    └─ Backend ACE ──HTTP─► ACE-Step :8001
       │                             └─ DiT + LM tùy chọn + LoRA
       └─ Khởi động/dừng dịch vụ, tải model, theo dõi tài nguyên

Worker còn chạy Essentia bằng tiến trình cục bộ để kiểm tra nhãn.
```

| Thành phần | Trách nhiệm | Mã nguồn chính |
|---|---|---|
| Desktop | Mở UI, quản lý tiến trình, điều phối đổi engine, sao lưu, quản lý model | `scripts/desktop.py` |
| UI | Biểu mẫu, nghe nhạc, hiển thị kết quả và trạng thái | `ui/app.py`, `ui/studio.css`, `ui/theme.py` |
| API | Kiểm tra yêu cầu, lưu dữ liệu, xếp hàng, nhận kết quả | `api/main.py`, `api/routes/` |
| SQLite | Lưu metadata, trạng thái tác vụ, worker và quan hệ dữ liệu | `api/db.py`, `api/migrations.py` |
| Worker | Nhận một tác vụ, chạy backend rồi gửi kết quả về API | `worker/worker.py` |
| Adapter ACE | Chuyển yêu cầu Music Studio thành lời gọi ACE-Step | `worker/backends/acestep.py` |
| Engine ACE-Step | Thực thi sinh nhạc, phân tích nhãn và huấn luyện | Dự án riêng, ví dụ `D:\ACE-Step-1.5` |
| Essentia | Phân tích tín hiệu audio để đối chiếu BPM/key | `shared/essentia_check.py` |

UI không gọi model trực tiếp. Worker không truy cập database. Riêng luồng train một máy đọc dataset từ thư mục dùng chung, rồi chuẩn bị dữ liệu trong vùng làm việc của ACE-Step. Vì vậy không thể mặc định chuyển worker sang máy khác mà giữ nguyên mọi đường dẫn.

## 3. Engine, DiT, LM và LoRA

| Khái niệm | Vai trò |
|---|---|
| ACE-Step engine | Phần mềm chạy AI và cung cấp REST API; có môi trường Python riêng |
| DiT | Model sinh audio; có thể tải nhiều phiên bản để chọn khi sử dụng |
| LM | Model hỗ trợ xử lý/lập kế hoạch âm nhạc và gán nhãn; có thể chọn phiên bản hoặc `Không dùng` |
| VAE/text encoder | Model phụ trợ dùng chung theo yêu cầu engine |
| LoRA | Adapter học từ dataset, gắn vào DiT tương thích để điều chỉnh phong cách |

Không cần cài một engine riêng cho mỗi DiT hoặc LM. Có thể lưu nhiều checkpoint trên ổ đĩa, nhưng bộ điều khiển chỉ khởi chạy **một cấu hình DiT + LM tùy chọn** tại một thời điểm.

DiT và LM có dropdown riêng, nên có thể giữ lựa chọn DiT khi đổi lựa chọn LM hoặc ngược lại. **Cơ chế áp dụng hiện tại vẫn dừng và chạy lại toàn bộ tiến trình ACE-Step**, chưa gỡ/nạp riêng từng phần trong cùng tiến trình.

Chọn `Không dùng` LM vẫn cho phép tạo nhạc khi DiT sẵn sàng. Luồng tự gán nhãn bằng AI hiện yêu cầu cả DiT và LM đã nạp. LoRA phải phù hợp base DiT; adapter mock không dùng được cho engine ACE thật.

## 4. Khởi động, đổi model và dừng

### Cách dùng hằng ngày

1. Bấm đúp `Music Studio.bat`.
2. Bấm **Khởi động** trong cửa sổ điều khiển.
3. Trình duyệt mở UI; API và worker được khởi động. Engine chưa được chạy chỉ vì mở ứng dụng.
4. Vào **Tạo nhạc → Model**, chọn DiT và LM.
5. Bấm **Áp dụng và khởi động lại engine**, chờ trạng thái sẵn sàng.
6. Làm việc trên các trang chức năng.
7. Chờ tác vụ xong rồi bấm **Dừng tất cả** khi kết thúc.

Đóng tab trình duyệt không dừng dịch vụ. Giữ cửa sổ điều khiển mở trong lúc sử dụng. Nút Dừng tất cả là thao tác dừng dịch vụ, không phải nút tạm dừng huấn luyện có thể tiếp tục sau đó.

### Khi đổi model

Thay dropdown mới chỉ thay lựa chọn dự kiến. Khi bấm Áp dụng:

1. API kiểm tra model thuộc danh sách được phép và không có tác vụ ngăn cản.
2. Nếu còn tác vụ đang chạy hoặc đang chờ, API thường từ chối bằng 409; admin chờ xong rồi áp dụng lại. **Chưa có yêu cầu đổi model tự xếp hàng để thực hiện sau.**
3. API chuyển engine sang `loading`, chặn nhận thêm tác vụ cần engine.
4. Desktop dừng tiến trình ACE-Step cũ cùng các tiến trình con thuộc quyền quản lý, giải phóng bộ nhớ của tiến trình cũ.
5. Desktop chạy ACE-Step với DiT/LM mới, kiểm tra health và đúng model đã nạp.
6. Chờ worker cập nhật danh sách model rồi báo `ready`; UI mở lại nút tạo nhạc khi các điều kiện đã đủ.
7. Nếu thất bại, trạng thái thành `error`, hiển thị lỗi để chọn lại cấu hình.

Ngoại lệ khi mới mở ứng dụng: có thể nạp cấu hình phù hợp với các job `queued` từ phiên trước để chúng tiếp tục. Việc chọn model qua UI chỉ có hiệu lực trong phiên, không sửa `.env` của engine.

### Tải hoặc xóa model

Trong cửa sổ desktop: **Dừng tất cả → Quản lý model DiT / LM**.

- Đọc danh mục từ môi trường ACE-Step, hiển thị model trên máy và dung lượng.
- Tải model được hỗ trợ; có thể hủy và giữ dữ liệu tải dở để thử tiếp.
- Kiểm tra config và file trọng số/shard trước khi coi model đã đủ.
- Chỉ xóa model nằm trong thư mục checkpoints được quản lý.
- Không dùng công cụ này để xóa VAE/text encoder dùng chung, dataset hay LoRA đã train.

Sau khi tải, khởi động lại Music Studio để danh sách lựa chọn engine được thiết lập lại. Công cụ quản lý model không cài thay toàn bộ môi trường ACE-Step.

## 5. Trang Tạo nhạc

### Các nhóm điều khiển

| Nhóm | Nội dung hiện có |
|---|---|
| Model, mặc định thu gọn | DiT/LM dự kiến, nút Áp dụng, model/LoRA đang dùng, seed, độ mạnh LoRA, làm mới |
| Tên bài | Tùy chọn; bỏ trống để ứng dụng tự đặt |
| Bạn muốn làm gì? | Tạo mới, Remix, Sửa đoạn |
| Giọng hát | Không lời hoặc Có lời; Có lời mở ô lời hát và ngôn ngữ |
| Caption | Mô tả phong cách bằng tiếng Anh: thể loại, cảm xúc, nhạc cụ… |
| Metadata cạnh caption | BPM, key/tông, nhịp; thông số chỉ định riêng được ưu tiên hơn mô tả |
| Thời lượng & số bản | Mặc định 180 giây, 2 bản; UI tạo mới chọn 10–240 giây, 1 hoặc 2 bản |
| Audio nguồn | Chỉ hiện với Remix/Sửa đoạn; thời lượng lấy từ nguồn 10–600 giây |
| Nâng cao | Số bước sinh, CFG khi phù hợp, tùy chọn LM và audio tham chiếu |

Seed `-1` là ngẫu nhiên; kết quả lưu seed thực tế. Độ mạnh LoRA từ 0 đến 1. BPM `0` là tự động; key và nhịp có lựa chọn tự động.

### Ba tác vụ

- **Tạo mới (`text2music`):** tạo từ caption, metadata, lời hát nếu có và cấu hình đã chọn.
- **Remix (`cover`):** dùng audio nguồn cùng mức giữ bản gốc; mức giữ mặc định 0,8.
- **Sửa đoạn (`repaint`):** chọn thời điểm bắt đầu/kết thúc trong audio nguồn và mức Nhẹ/Cân bằng/Mạnh. API kiểm tra khoảng thời gian hợp lệ; mức giữ phần ngoài đoạn còn phụ thuộc hành vi engine.

Audio nguồn và audio tham chiếu được tải qua API; chúng không tự trở thành bài trong danh sách Gán nhãn.

### Tùy chỉnh nâng cao

- Số bước `0`: dùng mặc định theo cấu hình/model; adapter dùng 8 cho turbo và 32 cho model khác khi không có cấu hình ghi đè.
- Turbo được kiểm tra giới hạn tối đa 20 bước.
- CFG hiện cho model phù hợp, không phải mọi phiên bản DiT đều hỗ trợ cùng cách.
- Với Tạo mới và LM được nạp: bật/tắt lập kế hoạch, cho phép cải thiện caption, nhiệt độ LM và mô tả yếu tố muốn tránh.
- Audio tham chiếu phong cách là đầu vào riêng, khác audio nguồn của Remix/Sửa đoạn.

Nút **Tạo nhạc** nằm trên danh sách Bài gần đây. Có phím tắt **Ctrl+Enter** trong caption. Trong lúc luồng tạo đang chạy, nút bị khóa và trạng thái được cập nhật. Khi tạo nhiều bản, UI gửi các tác vụ tạo; worker vẫn xử lý tuần tự, không có nghĩa chạy nhiều bài đồng thời trên GPU.

Đã bỏ nút **Kiểm tra prompt** và nhóm **Thêm phong cách** khỏi giao diện. Mã/API prompt refiner vẫn tồn tại, nhưng không phải bước bắt buộc của luồng tạo hiện tại.

## 6. Trang Thư viện

Lưu và hiển thị các bài đã sinh, với tìm kiếm và bộ lọc đánh giá. Mỗi bài có thể:

- Phát nhạc bằng thanh phát cố định ở đáy.
- Đánh giá thích/không thích.
- Xem chi tiết model, backend, LoRA, seed và tham số.
- Dùng lại cấu hình để nạp về biểu mẫu Tạo nhạc.

Metadata kết quả giúp tái lập và so sánh. Cùng seed không đảm bảo audio giống tuyệt đối nếu đổi model, phiên bản engine, môi trường hoặc tham số. Giao diện chưa có bảng so sánh A/B tự động giữa model gốc và LoRA.

## 7. Gán nhãn và kiểm chứng bằng Essentia

### Bước 1 — Thêm nhạc

Tải nhiều file WAV, MP3, FLAC, OGG hoặc OPUS. Chọn Có lời/Không lời cho lô tải lên và một trong hai chế độ:

- **Tự gán nhãn bằng AI** (mặc định): yêu cầu engine và worker gán nhãn ACE, LM đã nạp; API kiểm tra trước khi lưu file của yêu cầu này.
- **Nhập nhãn thủ công:** không cần engine sẵn sàng; bài được lưu để admin điền nhãn sau.

Mỗi bài ở chế độ AI có job gán nhãn riêng. Worker dùng chức năng dataset của ACE-Step để phân tích caption, BPM, key, nhịp và nhận dạng lời/ngôn ngữ khi phù hợp. Đây là suy luận của model, không phải thông tin chắc chắn.

### Bước 2 — Tự lưu và xem/sửa khi cần

Nhãn AI được lưu vào database khi tác vụ hoàn tất, không phụ thuộc việc trình duyệt còn mở. Hệ thống điền các trường còn thiếu và giữ nhãn đã lưu của admin.

| Trạng thái bài | Ý nghĩa |
|---|---|
| `pending` — Cần xem lại | Nhãn chưa đủ, kiểm chứng chưa đạt hoặc cần sửa thủ công |
| `approved` — Sẵn sàng dùng | Đủ điều kiện tự động hoặc admin đã lưu/chấp nhận để dùng |
| `rejected` | Bị loại khỏi lựa chọn tạo dataset; không đồng nghĩa xóa file |

Bài có caption, lời hát nếu là bài có lời và kiểm chứng đạt có thể tự chuyển sang sẵn sàng dùng. Lỗi AI không làm mất bài tải lên; có thể phân tích lại hoặc sửa thủ công.

Khung xem/sửa có nghe audio, caption, BPM/key/nhịp, giọng hát, lời, ngôn ngữ, tags tùy chọn, bảng đối chiếu và nút **Lưu thay đổi & tiếp theo**. Có chức năng xóa vĩnh viễn bài tải lên với xác nhận riêng.

### Essentia kiểm tra gì?

Essentia chạy trên CPU bằng executable riêng tại `tools/essentia/essentia_streaming_extractor_music.exe`. Bộ cài dùng extractor Windows từ UPF, kiểm tra SHA-256; thông tin nguồn và giấy phép trong `tools/essentia/README.md`.

Essentia phân tích tín hiệu để đối chiếu **BPM và key** với LM. Không kiểm chứng caption, lời hát hoặc nhịp như 3/4, 4/4. Nó cũng có thể đo sai, nên kết quả chỉ là lớp kiểm tra bổ sung.

Chính sách hiện tại:

- BPM lệch trong **max(5 BPM, 5% BPM đo được)** được coi là chênh lệch nhẹ.
- Key đồng âm khác tên được chuẩn hóa; cặp trưởng/thứ tương đối như C major/A minor được chấp nhận và giữ nhãn LM, kèm ghi chú.
- Lệch nặng, BPM nửa/gấp đôi hoặc kết quả đo chưa đủ tin cậy cần xem lại.
- Nếu thiếu executable, quá thời gian hoặc phân tích lỗi, hiển thị chưa kiểm chứng; không tự nhận là đã đạt.

Khi phép đo đủ tin cậy, kết quả đề xuất dùng BPM/key đo từ audio, kể cả khi có cảnh báo lệch; riêng cặp key trưởng/thứ tương đối giữ key LM. Các trường admin đã lưu vẫn được bảo vệ khỏi tự ghi đè. Vì vậy bảng đối chiếu giữ cả nhãn LM, số đo và giá trị đang lưu để admin quyết định.

Độ tin cậy dùng quy tắc nội bộ: BPM cần ít nhất 16 beat, biến thiên khoảng beat không quá 0,12 và onset rate ít nhất 0,2; key cần strength ít nhất 0,6 và ít nhất 2/3 profile đồng thuận. Đây **không phải xác suất chính xác**.

Kết quả lưu nhãn LM ban đầu, số đo, cảnh báo, chính sách so sánh và hash audio. Admin có thể nghe và chấp nhận thủ công dù có cảnh báo; thông tin chấp nhận được giữ trong bản xuất dataset. Nhãn cũ không có dữ liệu kiểm chứng vẫn có cơ chế tương thích, không tự được phân tích lại hàng loạt.

## 8. Dataset và quản lý dung lượng

### Tạo mới hoặc thêm vào dataset

Chỉ chọn các bài sẵn sàng dùng. Có hai chế độ:

1. **Tạo dataset mới:** nhập tên mới, chọn bài.
2. **Thêm nhạc vào dataset có sẵn:** chọn phiên bản mới nhất còn hoạt động, giữ dữ liệu cũ và thêm bài mới, bỏ qua bài trùng, lưu thành phiên bản tiếp theo.

Mỗi phiên bản là một snapshot. Thêm nhạc không sửa trực tiếp phiên bản cũ. API chặn bổ sung dựa trên phiên bản đã lỗi thời để tránh ghi nhầm khi nhiều yêu cầu xảy ra cùng lúc.

Nút **Dùng để finetune** tạo dataset rồi mở trang Finetune; **chưa tự bắt đầu train**.

### Lưu dạng thư mục

Dataset mới lưu tại `data/storage/datasets/`, gồm audio, caption, metadata JSON, lyrics khi có và `manifest.json`. Worker train dùng dữ liệu thư mục, không phải tạo ZIP rồi giải nén trong luồng thông thường.

Các file audio bất biến có thể dùng hard link trên cùng filesystem, nếu không được thì sao chép. Vì vậy tổng dung lượng cộng theo từng thư mục có thể cao hơn dung lượng vật lý thực tế; xóa một tên file chưa chắc giải phóng dung lượng nếu còn hard link khác.

ZIP chỉ tạo tạm khi admin bấm **Tải ZIP** để mang đi hoặc lưu trữ. Dataset ZIP cũ vẫn được hỗ trợ.

### Dọn dữ liệu và xóa

- Khi tạo dataset qua UI, bản tải lên của các bài mới đã đưa vào được tự dọn **sau khi** xuất, kiểm tra và lưu bản ghi dataset thành công.
- Audio và nhãn đã nằm trong dataset nên vẫn dùng để train được sau khi bản tải lên bị dọn.
- Nếu có dữ liệu đang được job dùng hoặc file không thể dọn, hệ thống chặn/báo cảnh báo thay vì coi như đã xóa hết.
- API tạo dataset có tùy chọn `cleanup_sources`; mặc định API là `false`, UI chủ động dùng `true`.
- Admin có thể xóa riêng bài tải lên hoặc phiên bản dataset bằng các khung quản lý có xác nhận.
- Xóa phiên bản dataset giữ dấu lịch sử để không tái sử dụng số phiên bản và giữ liên kết lịch sử LoRA. LoRA đã train không bị xóa theo thao tác này.

Không có chính sách tự động xóa toàn bộ bài sinh ra, LoRA hay cache chỉ vì đã lâu không dùng. Công cụ dọn mock riêng có tính chất xóa theo dây chuyền, không phải công cụ dọn mọi dữ liệu thật.

## 9. Finetune

Finetune hiện huấn luyện **adapter LoRA trên DiT**, không phải huấn luyện LM hay toàn bộ trọng số model gốc.

### Tham số admin chọn

| Trường | Ý nghĩa / mặc định UI |
|---|---|
| Phiên bản dataset | Snapshot đã chuẩn bị |
| Base model | DiT tương thích, phải được áp dụng cho engine |
| Tên phong cách | Nhóm các LoRA theo mục đích |
| Tên LoRA | Tùy chọn; để trống sẽ tự đặt |
| Rank | Kích thước adapter; mặc định 16 |
| Learning rate | Tốc độ cập nhật; mặc định `0.0001` |
| Epochs | Số vòng qua dataset; mặc định 10 |

Các giá trị mặc định là cấu hình biểu mẫu, không đảm bảo tối ưu cho mọi dataset.

### Luồng xử lý

1. API kiểm tra dataset/base model và xếp job train.
2. Worker chuẩn bị audio/nhãn trong `ACESTEP_WORK_DIR`, nằm trong vùng ACE-Step cho phép.
3. Làm sạch trạng thái model khi cần, scan dataset rồi tiền xử lý thành tensor.
4. Gọi ACE-Step train và thăm dò tiến độ/loss.
5. Lưu adapter final về `data/storage/loras/`, tạo bản ghi LoRA.
6. Khởi tạo lại model theo luồng adapter và dọn thư mục làm việc khi an toàn.

Adapter hiện đặt batch size 1, gradient accumulation 1, alpha bằng `min(512, 2 × rank)`, gradient checkpointing theo cấu hình và **chỉ thu bản final**. Một số xử lý này là cấu hình nội bộ, chưa có điều khiển riêng trong UI.

Trang Finetune có trạng thái, log tiến độ/loss và bảng LoRA theo phong cách. Sau train, vào Tạo nhạc chọn LoRA đúng base model và điều chỉnh độ mạnh để nghe thử.

Chưa có nút dừng train chuyên dụng, resume checkpoint, danh sách checkpoint giữa chừng, biểu đồ loss riêng hay bảng nghe A/B tự động. Loss giảm chưa chứng minh chất lượng nghe tốt; admin vẫn cần thử nhiều mô tả và seed.

## 10. Theo dõi hệ thống và tài nguyên

### Trang Hệ thống trong trình duyệt

- Địa chỉ API, cấu hình prompt refiner và trạng thái bật/tắt token.
- Backend theo ba loại job: tạo nhạc, gán nhãn, train.
- Worker online/offline, backend và lần liên lạc cuối.
- Thống kê theo loại/trạng thái job, đếm trên toàn bộ bảng job. Đây là thống kê các trạng thái, có thể gồm cả `done`/`failed`, không chỉ job đang chờ.
- Model khả dụng ở dạng JSON: DiT/LM do worker báo cáo, backend và số worker online.
- 30 job gần nhất: ID, loại, trạng thái, phần trăm, worker, thời gian và lỗi rút gọn.
- Tự làm mới mỗi 5 giây và có nút làm mới thủ công.

Thông tin worker/model được cập nhật theo chu kỳ nên có thể trễ sau khi bật/tắt engine. Trang này hiện chủ yếu để xem, chưa có nút hủy job hoặc khởi động lại dịch vụ.

### Cửa sổ desktop

Bảng tài nguyên hiển thị từng thành phần được quản lý: PID, CPU, RAM RSS và thông tin GPU/VRAM khi đọc được. CPU/RAM cộng cả tiến trình con; CPU được chuẩn hóa theo số CPU logic. RSS là bộ nhớ tiến trình đang hiện diện trong RAM, bao gồm runtime và thư viện, không chỉ trọng số model; cộng RSS cũng có thể tính trùng vùng nhớ dùng chung.

GPU được đọc qua `nvidia-smi`. Trên Windows WDDM có thể không lấy được VRAM riêng từng PID. Dòng ACE-Step khi đó dùng **tổng VRAM GPU đang sử dụng**, gồm cả ứng dụng khác; không thể diễn giải thành lượng VRAM của riêng DiT/LM. Bảng lấy mẫu khoảng mỗi 2 giây; khi chưa chạy engine vẫn có thể có VRAM từ desktop hoặc chương trình khác.

## 11. Hàng đợi và phục hồi tác vụ

Ba loại job: `generate`, `autolabel`, `train`.

```text
queued ──worker nhận──► running ──thành công──► done
                          └──────lỗi────────► failed

Worker mất liên lạc / khởi động lại: job đang giữ có thể về queued.
```

- SQLite vừa lưu dữ liệu vừa giữ hàng đợi; chưa cần Redis hay dịch vụ queue riêng.
- Nhận job trong transaction để tránh hai worker nhận cùng bản ghi.
- Worker hiện chạy tuần tự, một job mỗi lần; các luồng tạo nhạc, gán nhãn và train dùng chung worker/GPU không chạy song song.
- Heartbeat khoảng 10 giây; khoảng 30 giây gửi lại model/cảnh báo.
- Mặc định worker online nếu còn liên lạc trong 30 giây; job của worker stale có thể được trả lại sau ngưỡng 60 giây qua cơ chế nhận/đăng ký job.
- Worker khởi động với cùng ID có thể trả các job cũ của nó về hàng đợi.
- Job `failed` được giữ để xem lỗi; chưa có nút tự thử lại mọi job lỗi trong UI.

Đưa về hàng đợi là **chạy lại tác vụ**, không phải tiếp tục huấn luyện đúng bước đã dừng. Việc tránh nhận trùng bản ghi cũng không phải bảo đảm chỉ thực thi đúng một lần khi tiến trình chết sau khi engine đã xử lý nhưng trước khi gửi kết quả.

Trạng thái LoRA, dataset và train trong ACE-Step mang tính dùng chung. Không nên chạy thêm công cụ khác hoặc nhiều worker cùng điều khiển một engine đang phục vụ Music Studio.

## 12. Dữ liệu và cấu trúc mã nguồn

```text
D:\ai_music_studio\
├─ Music Studio.bat          Mở cửa sổ điều khiển
├─ .env                      Cấu hình riêng máy, không chia sẻ khóa bí mật
├─ .venv\                    Môi trường Python của Music Studio
├─ api\                      API, SQLite, storage, điều khiển engine
├─ worker\                   Vòng lặp nhận job và backend ACE/mock
├─ ui\                       Gradio, CSS, client gọi API
├─ shared\                   Schema, cấu hình, audio, dataset, Essentia
├─ prompt_refiner\           Refiner noop/Claude còn giữ ở mã nguồn
├─ scripts\                  Desktop, cài đặt, model, tài nguyên, chạy dịch vụ
├─ tests\                    Kiểm thử hành vi và tích hợp
├─ tools\essentia\           Extractor và thông tin nguồn/giấy phép
└─ data\                     Dữ liệu vận hành
   ├─ app.db                 Metadata và hàng đợi SQLite
   ├─ storage\
   │  ├─ uploads\            Audio tải lên / đầu vào
   │  ├─ outputs\            Audio sinh ra
   │  ├─ datasets\           Các snapshot dataset
   │  └─ loras\              Adapter đã train
   ├─ ui_cache\              Bản file tải về để Gradio phát/tải
   └─ logs\                  Nhật ký và khóa cửa sổ desktop

D:\ACE-Step-1.5\
├─ .env                      Cấu hình engine
├─ .venv\                    Môi trường Python riêng của ACE-Step
├─ checkpoints\             DiT, LM và model phụ trợ
└─ music_studio_work\        Dữ liệu làm việc train/nhãn/cache adapter
```

`DATA_DIR`, thư mục engine và thư mục checkpoints có thể được cấu hình khác ví dụ này. Một số dữ liệu tạm còn nằm trong thư mục tạm hệ điều hành/Gradio; đóng ứng dụng không đồng nghĩa mọi cache đều được xóa.

Database gồm các bảng chính: `jobs`, `tracks`, `datasets`, `loras`, `generations`, `workers`, `schema_version`. Migration tự áp dụng khi tạo kết nối ứng dụng; mã hiện có migration đến v4, gồm trường lời hát/ngôn ngữ và dấu xóa dataset.

Database và storage cần được giữ cùng nhau. Chỉ chép audio hoặc chỉ chép `app.db` sẽ thiếu metadata hoặc thiếu file tương ứng.

## 13. Cấu hình và cài đặt

### Hai môi trường riêng

Music Studio dùng Python 3.11 và `.venv` riêng. ACE-Step cần được cài độc lập cùng môi trường CUDA/PyTorch phù hợp. `scripts/setup.bat` chỉ thiết lập phía Music Studio và Essentia, không cài hoàn chỉnh engine/CUDA.

Chạy trong PowerShell:

```powershell
cd D:\ai_music_studio
.\scripts\setup.bat
& '.\Music Studio.bat'
```

Bấm đúp file BAT cũng được. `setup.bat` tạo `.venv`, cài `requirements.txt`, thử cài Essentia và tạo `.env` từ `.env.dev` nếu chưa có. Thư viện tùy chọn trong `requirements-optional.txt` chỉ cài khi yêu cầu qua `WITH_OPTIONAL=1`.

### Các nhóm cấu hình Music Studio

| Biến | Vai trò |
|---|---|
| `API_HOST`, `API_PORT`, `API_URL` | Bind và địa chỉ API; mặc định cổng 8000 |
| `UI_HOST`, `UI_PORT` | Bind giao diện; mặc định cổng 7860 |
| `API_TOKEN` | Token chung gửi bằng header `X-Token` |
| `DATA_DIR` | Gốc database/storage/logs |
| `GENERATE_BACKEND` | Backend tạo nhạc: `ace` hoặc `mock` |
| `AUTOLABEL_BACKEND` | Backend gán nhãn; AI thật cần `ace` |
| `TRAIN_BACKEND` | Backend train: `ace` hoặc `mock` |
| `WORKER_ID`, `WORKER_TYPES` | Danh tính và loại tác vụ worker nhận |
| `WORKER_POLL_INTERVAL` | Khoảng thăm dò job; mặc định 2 giây |
| `MUSIC_STUDIO_ENGINE_DIR` | Đường dẫn engine cho desktop |
| `ACESTEP_URL`, `ACESTEP_API_KEY` | Kết nối đến engine; mặc định cổng 8001 |
| `ACESTEP_WORK_DIR` | Vùng làm việc phải nằm trong vùng engine cho phép |
| `ACESTEP_TIMEOUT`, `ACESTEP_TRAIN_TIMEOUT` | Giới hạn chờ sinh nhạc và train |
| `ACESTEP_INFERENCE_STEPS` | Mặc định số bước khi không chỉ định riêng |
| `ESSENTIA_ENABLED`, `ESSENTIA_TIMEOUT` | Bật kiểm chứng, giới hạn thời gian |
| `ESSENTIA_EXECUTABLE` | Ghi đè đường dẫn extractor nếu cần |
| `PROMPT_REFINER` | `noop` hoặc `claude`; luồng kiểm tra prompt đã ẩn khỏi UI |

Ví dụ một máy dùng ACE thật, không chứa khóa:

```dotenv
GENERATE_BACKEND=ace
AUTOLABEL_BACKEND=ace
TRAIN_BACKEND=ace
MUSIC_STUDIO_ENGINE_DIR=D:/ACE-Step-1.5
ACESTEP_URL=http://127.0.0.1:8001
ACESTEP_WORK_DIR=D:/ACE-Step-1.5/music_studio_work
ESSENTIA_ENABLED=true
```

Biến môi trường sẵn có ưu tiên hơn file được chỉ định bởi `ENV_FILE`, rồi đến `.env` mặc định. Bộ điều khiển đọc lại cấu hình khi khởi động sau khi đã dừng. Không sửa `.env` rồi kỳ vọng các tiến trình đang chạy tự cập nhật ngay.

### Cấu hình phía ACE-Step

Các khóa liên quan gồm `ACESTEP_CONFIG_PATH` (DiT), `ACESTEP_INIT_LLM` (bật LM), `ACESTEP_LM_MODEL_PATH` (LM) và `ACESTEP_CHECKPOINTS_DIR` nếu đổi nơi lưu model. Desktop dùng cấu hình engine rồi ghi đè lựa chọn phiên DiT/LM khi chạy; chủ động bỏ cấu hình model phụ thứ hai/thứ ba để không nạp nhiều DiT.

Nếu engine bật API key, giá trị `ACESTEP_API_KEY` hai phía phải khớp. Không đưa giá trị khóa vào tài liệu, ảnh chụp hoặc gói chia sẻ.

### Chế độ mock và chạy terminal

Đặt cả ba backend thành `mock` để thử giao diện/hàng đợi không cần GPU. Nhạc và adapter mock chỉ phục vụ kiểm thử, không phản ánh chất lượng hay tốc độ ACE.

```powershell
.\scripts\start_all.bat
```

Lệnh này chạy API/worker/UI theo luồng terminal; không thay thế toàn bộ chức năng quản lý engine của desktop. Chọn **một cách khởi động mỗi phiên**. Không chạy thêm ACE hoặc `start_all.bat` khi desktop đã chiếm các cổng.

## 14. API chính

Tài liệu tương tác khi API chạy: `http://127.0.0.1:8000/docs`.

| Nhóm | Endpoint tiêu biểu | Mục đích |
|---|---|---|
| Sức khỏe | `GET /health` | Kiểm tra API, công khai |
| Theo dõi | `GET /system`, `GET /models`, `GET /jobs`, `GET /jobs/{id}` | Worker, model và tiến độ |
| Engine | `GET /engine`, `POST /engine/apply` | Xem/đề nghị áp dụng cấu hình |
| Engine nội bộ | `POST /engine/setup`, `/engine/poll`, `/engine/result` | Desktop điều phối bằng owner và revision |
| Sinh nhạc | `POST /generate/audio`, `POST /generate` | Tải đầu vào, tạo job |
| Kết quả | `GET /generations`, `GET /generations/{id}`, `POST /generations/{id}/rating` | Danh sách, chi tiết, đánh giá |
| Nhạc tham chiếu | `POST /tracks`, `GET /tracks`, `PUT /tracks/{id}` | Upload và sửa nhãn |
| Gán nhãn/xóa | `POST /tracks/{id}/autolabel`, `DELETE /tracks/{id}` | Phân tích lại, xóa bài tải lên |
| Dataset | `POST /datasets`, `GET /datasets`, `GET /datasets/{id}/download`, `DELETE /datasets/{id}` | Tạo/bổ sung, tải ZIP, xóa phiên bản |
| Huấn luyện | `POST /train`, `GET /loras` | Tạo job train và liệt kê LoRA |
| File | `GET /files/{path}` | Đọc file trong storage hợp lệ |
| Worker nội bộ | `/worker/register`, `/worker/heartbeat`, `/worker/claim`, `/worker/jobs/{id}/…` | Nhận job, cập nhật và gửi kết quả |
| Prompt còn giữ | `POST /prompt/refine` | Refiner không còn nút gọi trực tiếp trên UI |

Nếu cấu hình `API_TOKEN`, các router chức năng yêu cầu `X-Token`. Token này là khóa chung giữa các thành phần, không phải hệ thống đăng nhập/phân quyền người dùng. Cấu hình mặc định dùng localhost; chưa có lớp HTTPS/reverse proxy trong luồng desktop.

## 15. Sao lưu, chuyển máy và cập nhật

### Sao lưu hiện có

1. Chờ tác vụ kết thúc và bấm **Dừng tất cả**.
2. Bấm **Sao lưu dữ liệu**.
3. Chọn file ZIP ở ngoài `DATA_DIR`.

Chức năng tạo snapshot SQLite và đóng gói `data/storage`. **Không gồm checkpoint gốc, môi trường Python, mã nguồn, `.env`, log hoặc UI cache.** Model gốc có thể tải lại; `.env` cần lưu riêng an toàn nếu muốn giữ cấu hình.

Chưa có nút khôi phục tự động. Khi khôi phục thủ công, dừng dịch vụ, giữ bản sao dữ liệu hiện tại, đặt `app.db` và storage vào `DATA_DIR` đúng cấu trúc, rồi kiểm tra đường dẫn cấu hình.

### Chuyển sang máy admin khác

Gửi mã nguồn và hướng dẫn; cài môi trường Music Studio và ACE-Step trên máy đích, chỉnh `.env`, tải DiT/LM phù hợp phần cứng. Nếu cần dữ liệu cũ thì mang thêm bản sao lưu. Không cần gửi toàn bộ checkpoint nếu máy đích tải được.

Không nên xem `.venv` từ máy cũ là gói cài di động; hãy tạo lại môi trường. Thư mục làm việc engine chứa cache LoRA theo đường dẫn nên có thể cần tái tạo trên máy mới.

### Cập nhật phiên bản

Mỗi bản phát hành là một gói `MusicStudio-<phiên bản>.zip` do `scripts/make_release.py` tạo (chạy test, kèm `requirements.lock` khóa phiên bản thư viện). Trên máy admin: dừng tất cả, đóng cửa sổ điều khiển, giải nén gói ra thư mục riêng và chạy `Cap nhat Music Studio.bat`. Trình cập nhật sao lưu code và `app.db` vào `backups\`, thay code do gói quản lý, cài thư viện khi lock đổi, chạy migration và thử khởi tạo; lỗi thì tự khôi phục. `Khoi phuc ban truoc.bat` trả code về bản trước mà giữ database. Kênh online: `make_release.py` tạo thêm `latest.json`; khi file này được đăng lên địa chỉ cố định (ghi trong gói hoặc `UPDATE_URL`), cửa sổ điều khiển tự báo bản mới (lúc mở, mỗi 6 giờ) và nút **Cập nhật** tải gói, kiểm tra SHA-256 rồi chạy cùng trình cập nhật đó; giao diện web hiện thông báo ở thanh bên. Cập nhật ACE-Step là thao tác riêng. Chi tiết trong [DEPLOY.md](DEPLOY.md).

## 16. Xử lý lỗi thường gặp

| Hiện tượng | Kiểm tra / cách xử lý |
|---|---|
| WinError 10048, cổng 8000/7860/8001 bị chiếm | Thường đang mở trùng dịch vụ; dùng cửa sổ sở hữu dịch vụ để dừng, không chạy thêm phiên |
| UI mở nhưng chưa tạo nhạc được | Mở Model, chọn và Áp dụng; chờ engine `ready` |
| 409: engine chưa sẵn sàng | Chờ nạp xong hoặc đọc lỗi và áp dụng cấu hình khác |
| 409: còn tác vụ khi đổi model | Chờ cả tác vụ đang chạy/chờ hoàn tất rồi áp dụng lại |
| 409: chưa có worker gán nhãn ACE | Kiểm tra `AUTOLABEL_BACKEND=ace`, dừng/khởi động lại worker qua desktop, nạp DiT và LM |
| Nhãn AI lưu rồi nhưng chưa vào danh sách dataset | Xem thiếu caption/lời hoặc cảnh báo Essentia; nghe và lưu chấp nhận nếu phù hợp |
| Thiếu Essentia | Chạy `scripts/setup_essentia.py` bằng Python `.venv`, kiểm tra log/mạng |
| Không thấy model vừa tải | Đảm bảo tải đủ checkpoint; dừng/khởi động để làm mới danh sách engine |
| Nạp LM lỗi / hết VRAM | Chọn LM nhỏ hơn hoặc không dùng LM cho tạo nhạc; tắt ứng dụng GPU khác |
| Train lỗi đường dẫn | `ACESTEP_WORK_DIR` phải nằm trong thư mục làm việc ACE-Step cho phép |
| Train loss NaN hoặc API status 500 | Xem log worker/engine, chất lượng/thời lượng audio và learning rate; không coi job đó là thành công |
| VRAM vẫn cao sau train | Có thể liên quan trạng thái/tài nguyên phía engine; chờ tác vụ xong rồi dừng/chạy lại engine qua desktop |
| Xóa dữ liệu bị chặn | Có job đang sử dụng dữ liệu hoặc file bị khóa; xử lý nguyên nhân rồi thử lại |

Nhật ký thường dùng: `data/logs/api.log`, `worker.log`, `ui.log`, `acestep.log`, `model-download.log`. Desktop mở lại file log dịch vụ bằng chế độ ghi mới mỗi lần khởi chạy; hãy chép log cần giữ trước khi chạy lại. `desktop.lock` là khóa chống mở nhiều cửa sổ điều khiển, không phải log lỗi.

## 17. Giới hạn và hướng phát triển

| Đang có | Chưa có / giới hạn |
|---|---|
| Các lớp UI/API/worker/engine tách mã và tiến trình | Chưa phải bộ triển khai phân tán đầy đủ; train thư mục phụ thuộc đường dẫn cùng máy |
| Chọn DiT và LM riêng | Áp dụng vẫn restart cả engine |
| Chặn đổi model khi bận | Chưa tự xếp lịch đổi sau tác vụ |
| Train LoRA, log và adapter final | Chưa resume, checkpoint UI, biểu đồ loss, nút dừng train chuyên dụng |
| Nhãn AI + kiểm chứng BPM/key | Chưa kiểm chứng tự động nội dung caption/lời/nhịp |
| Metadata và dùng lại cấu hình | Chưa có panel nghe A/B tự động |
| Quản lý tải/xóa checkpoint | Chưa tự cài hay cập nhật engine ACE-Step |
| Sao lưu dữ liệu; cập nhật online một nút, tự khôi phục khi lỗi và quay lại bản trước | Chưa có nút phục hồi bản sao lưu dữ liệu; gói chưa được ký số (chỉ kiểm SHA-256 qua HTTPS) |
| Token chung, mặc định localhost | Chưa có tài khoản, phân quyền, HTTPS và hạn mức nhiều người dùng |

Mã có hỗ trợ đăng ký nhiều worker, nhưng một worker tuần tự chỉ bảo đảm không chạy đồng thời trong chính worker đó. Chưa có khóa GPU toàn cục giữa nhiều worker khác nhau. Với mục tiêu một máy admin, tiếp tục dùng một worker cho engine hiện tại.

Tính tương thích phần cứng phụ thuộc engine, CUDA, driver và dung lượng model. Không có cam kết mọi cặp DiT/LM đều chạy trên mọi GPU. Các số đo trong tài liệu cũ là kết quả một cấu hình/thời điểm, không phải yêu cầu tài nguyên cố định hay phép đo hiện tại.

## 18. Đối chiếu và kiểm thử

Tài liệu được viết từ mã nguồn, không dựa riêng vào README cũ. Các điểm đối chiếu chính:

| Chủ đề | Nguồn |
|---|---|
| Layout/chức năng UI | `ui/app.py`, `ui/studio.css` |
| Khởi động, đổi engine, backup | `scripts/desktop.py`, `api/engine_control.py` |
| Model tải về, tiến trình, tài nguyên | `scripts/model_store.py`, `scripts/process_tree.py`, `scripts/resources.py` |
| Cấu hình và kiểm tra đầu vào | `shared/config.py`, `shared/schemas.py` |
| Luồng ACE và tham số train | `worker/backends/acestep.py` |
| Tự lưu nhãn, trạng thái và migration | `api/db.py`, `api/migrations.py`, `api/routes/worker.py` |
| Dataset, dọn dữ liệu | `api/routes/datasets.py`, `api/storage.py`, `api/data_cleanup.py`, `shared/dataset_files.py` |
| Quy tắc Essentia | `shared/essentia_check.py`, `tools/essentia/README.md` |

Repository có kiểm thử về queue, engine, backend, upload/nhãn, Essentia, snapshot dataset, xóa dữ liệu, train adapter và desktop. Nhiều bài kiểm thử dùng mock hoặc HTTP giả; chúng không thay thế thử chất lượng với GPU/model thật.

Lệnh chạy kiểm thử cho người phát triển:

```powershell
cd D:\ai_music_studio
.\.venv\Scripts\python.exe -m pytest -q
```

Lần cập nhật tài liệu này chỉ đọc mã nguồn và kiểm tra tài liệu; không khởi động lại dịch vụ, không chạy train/sinh nhạc thật và không xóa dữ liệu.

Tài liệu liên quan: [hướng dẫn admin](HUONG_DAN.md), [README](README.md), [ghi chú triển khai](DEPLOY.md). Khi các tài liệu cũ khác với giao diện hiện tại, đối chiếu mã nguồn và các giới hạn đã ghi ở đây trước khi vận hành.
