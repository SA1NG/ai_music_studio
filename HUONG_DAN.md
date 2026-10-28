# Music Studio dành cho admin thử nghiệm trên Windows

## Sử dụng hằng ngày

1. Bấm đúp **Music Studio.bat** để mở cửa sổ điều khiển.
2. Bấm **Khởi động** để mở giao diện; bước này chưa nạp model AI. Trong thanh **Model**, chọn DiT/LM rồi bấm **Áp dụng và nạp model**. Khi model sẵn sàng, nút **Tạo nhạc** được bật.
3. Tạo nhạc thử, upload/duyệt nhạc tham chiếu, tạo dataset và huấn luyện ở các tab tương ứng.
4. Khi xong, bấm **Dừng tất cả**, rồi đóng cửa sổ điều khiển.

Không chạy thêm `start_all.bat` hay `acestep-api` khi đang dùng bộ điều khiển.
Đóng trình duyệt không dừng GPU/server. Nếu còn tác vụ chạy hoặc chờ, bộ điều khiển yêu cầu chờ hoàn tất trước khi dừng.
Nếu khởi động lỗi, mở **Nhật ký**; mỗi dịch vụ có một file log. Không cần tự đọc log trong lúc sử dụng bình thường.

Bảng **Tài nguyên đang sử dụng** trong cửa sổ điều khiển cập nhật khoảng 2 giây/lần,
hiển thị CPU, RAM RSS và PID theo UI, API, worker, ACE-Step và tiến trình tải model.
CPU được tính theo tổng số luồng CPU của máy; RAM gồm cả tiến trình con và có thể chứa vùng nhớ chia sẻ.
VRAM ở dòng ACE-Step là **tổng bộ nhớ GPU đang dùng**, gồm cả ứng dụng khác, không phải số đo riêng của DiT/LM.
Số liệu GPU được đọc từ NVIDIA; nếu không có công cụ/driver phù hợp, CPU/RAM vẫn được hiển thị.

## Tạo mới, Remix và Sửa đoạn

- **Tạo mới:** nhập caption, chọn Không lời hoặc Có lời. Nếu Có lời, nhập lời hát và ngôn ngữ; có thể chia lời bằng `[Verse]`, `[Chorus]`. Audio tham chiếu phong cách là tùy chọn trong phần nâng cao.
- **Remix:** tải audio nguồn, mô tả cách phối mong muốn rồi chỉnh **Mức giữ bản gốc**. Có thể nghe audio nguồn ngay trong form.
- **Sửa đoạn:** tải audio nguồn, nhập mốc bắt đầu/kết thúc theo giây và chọn mức sửa Nhẹ/Cân bằng/Mạnh. Khoảng sửa phải nằm trong audio nguồn.
- Audio nguồn Remix/Sửa đoạn phải dài 10–600 giây; kết quả dùng thời lượng nguồn. Các file này không tự đưa vào thư viện gán nhãn/dataset.
- **BPM, key và nhịp** nằm ngay dưới caption. BPM `0` và các lựa chọn **Tự động** để model tự xử lý; giá trị chỉ định riêng được ưu tiên hơn nội dung caption.
- Trong **Tùy chỉnh nâng cao**, số bước `0` dùng cấu hình ứng dụng nếu có, nếu không dùng 8 bước cho Turbo và 32 bước cho model khác. Turbo nhận tối đa 20 bước. CFG chỉ hiện với model không phải Turbo; tùy chọn LM chỉ hiện khi có LM được chọn.
- Thanh **Model** vẫn thu gọn DiT, LM, LoRA, độ mạnh LoRA và seed. Chọn model rồi bấm Áp dụng để nạp.
- Nút **↺** trên bài đã tạo khôi phục cả tác vụ, lời hát, thông số và audio nguồn/tham chiếu. **Ctrl+Enter** trong caption tạo nhạc khi nút Tạo nhạc sẵn sàng.

Các thao tác này dùng ACE-Step thật khi backend là `ace`. Backend `mock` chỉ phục vụ kiểm thử luồng và tạo âm thanh giả lập.

## Chuẩn bị nhạc để finetune

Trang **Gán nhãn & dataset** gồm ba bước:

1. **Thêm nhạc & tạo nhãn:** mặc định chọn **Tự gán nhãn bằng AI**. Chọn file và Không lời/Có lời rồi bấm **Thêm nhạc & tự gán nhãn**. Mỗi bài được xếp một job AI; kết quả tự hiện ở bước 2. Cần nạp DiT và LM trước. Nếu chưa nạp model, chọn **Nhập nhãn thủ công** để chỉ tải file lên.
2. **Xem / sửa nhãn (tùy chọn):** AI tự lưu nhãn thành công; bài đủ caption và lời hát (nếu có) được đưa ngay vào danh sách sẵn sàng dùng. Không bắt buộc kiểm tra hoặc bấm lưu từng bài. Chỉ mở bài khi muốn nghe, xem hoặc sửa; khi sửa, bấm **Lưu thay đổi & tiếp theo**. Bài nhập thủ công hoặc AI chưa tạo đủ nhãn vẫn cần bổ sung rồi lưu. **Loại khỏi bộ dữ liệu** bỏ bài khỏi danh sách được dùng để train.
3. **Tạo bộ dữ liệu:** chọn **Tạo dataset mới** để đặt tên mới, hoặc **Thêm nhạc vào dataset có sẵn** rồi chọn dataset. Chọn các bài đã kiểm tra và bấm **Dùng để finetune**. Ứng dụng tạo một phiên bản dataset và mở trang Finetune với dataset đó được chọn; chưa bắt đầu train.

Dataset mới được lưu thành **thư mục** chứa audio và nhãn. Khi thêm nhạc, ứng dụng giữ dữ liệu của phiên bản gốc, thêm các bài mới và bỏ qua ID bài trùng. Ví dụ `lofi v1` có 10 bài, thêm 3 bài mới sẽ tạo `lofi v2` gồm 13 bài. Các file không đổi được liên kết giữa các phiên bản nếu ổ đĩa hỗ trợ; nếu không thì sao chép. Phiên bản cũ vẫn giữ nguyên. Danh sách bổ sung chỉ hiện phiên bản mới nhất; nếu có phiên bản mới được tạo ở nơi khác, bấm **Làm mới dataset** trước khi tiếp tục.

Sau khi tạo và kiểm tra dataset thành công bằng giao diện, các bài vừa đưa vào dataset được xóa khỏi danh sách tải lên, kèm file gốc và cache có thể dọn được. Nếu file còn nằm trong tác vụ đang chạy/chờ hoặc không xóa được, dataset vẫn được giữ và giao diện báo phần chưa dọn. Nếu tạo dataset thất bại, bản tải lên được giữ nguyên.

Trong **Quản lý bộ dữ liệu**, nút **Tải ZIP** chỉ đóng gói theo yêu cầu để tải xuống; không lưu thêm ZIP thường trực ở server. Worker trên cùng máy dùng thư mục dataset trực tiếp, không cần tải/giải nén ZIP; ACE-Step nhận bản liên kết/sao chép vào thư mục làm việc của engine. Dataset ZIP cũ vẫn tải và train được, và có thể làm nguồn cho phiên bản thư mục mới.

Để dọn thủ công:

- **Xóa bài tải lên:** chọn bài ở phần Xem/sửa, mở mục Xóa bài tải lên, xác nhận rồi bấm xóa. Audio trong dataset đã tạo không bị ảnh hưởng.
- **Xóa phiên bản dataset:** mở Quản lý bộ dữ liệu, chọn phiên bản, xác nhận rồi bấm xóa. Xóa dữ liệu trên đĩa của phiên bản đó, giữ LoRA đã train và lịch sử phiên bản. Số phiên bản đã xóa không được dùng lại.
- Không xóa file đang được tác vụ chạy/chờ sử dụng. Việc xóa không ảnh hưởng các phiên bản dataset khác.

Tags nằm trong **Thông tin bổ sung**. Lịch sử phiên bản và tải ZIP nằm trong **Quản lý bộ dữ liệu**. Nhạc không lời và nhạc có lời được xuất sidecar phù hợp cho ACE-Step; dữ liệu cũ giữ nguyên và mặc định là nhạc không lời.

Để tự điền nhãn bằng AI:

1. Trong **Tạo nhạc → Model**, nạp DiT và một LM. Không chọn “Không dùng” ở LM.
2. Chế độ tự gán nhãn khi tải lên đã thực hiện phân tích cho từng bài. Ở bước **Kiểm tra nhãn**, chọn bài để xem kết quả. Muốn chạy lại, bấm **Phân tích lại bằng AI**; với bài có lời có thể bật nhận dạng lời hát.
3. Chờ tác vụ trong hàng đợi. Backend tự lưu caption, BPM, key, nhịp và lời/ngôn ngữ khi phù hợp, giữ nhãn bạn đã lưu và không đưa lại các bài đã loại vào dataset.
4. Bài đủ nhãn tự xuất hiện ở bước Tạo bộ dữ liệu, ngay cả khi không mở phần xem/sửa hoặc đã đóng trình duyệt. Chỉ cần bấm **Lưu thay đổi & tiếp theo** nếu bạn chỉnh sửa. Bài gán nhãn thất bại hoặc thiếu lời hát vẫn cần xử lý; kết quả của bài khác không được điền vào form đang xem.

Gán nhãn thật yêu cầu worker dùng `AUTOLABEL_BACKEND=ace`. Cấu hình Windows hiện tại và mẫu GPU đã đặt sẵn; khởi động lại Music Studio sau khi cập nhật. Bước nhập/sửa nhãn thủ công vẫn dùng được khi chưa nạp model.

### Đối chiếu LM bằng Essentia

Sau khi LM tạo nhãn, Essentia đo BPM và key từ audio trên CPU. Phần Xem/sửa hiển thị bảng **LM / Essentia / Đang lưu**. Nhịp, caption và lời hát không được Essentia xác minh trong phiên bản này.

- Khi phép đo đủ rõ và hai nguồn khớp, bài được lưu và sẵn sàng dùng như trước. BPM/key đo được được ưu tiên cho các trường chưa có nhãn do admin lưu.
- Nếu lệch (kể cả BPM nửa/gấp đôi), key chưa rõ, nhịp beat không ổn định hoặc không chạy được Essentia, bài ở trạng thái **Cần xem lại**, chưa tự chọn vào dataset. Admin nghe, sửa khi cần rồi bấm **Lưu thay đổi & tiếp theo** để chấp nhận.
- Essentia cũng có thể sai. Ứng dụng dùng ít nhất 16 beat, hệ số biến thiên khoảng beat CV ≤ 0.12 và onset rate ≥ 0.2 để xem BPM đủ ổn định; key cần strength ≥ 0.6 và ít nhất 2/3 profile cùng key. Đây là ngưỡng kiểm tra của ứng dụng, không phải xác suất đúng.
- So sánh BPM cho phép sai lệch tối đa 5 BPM hoặc 5% của BPM đo được (lấy ngưỡng lớn hơn). Key tương đương như G# minor và Ab minor được coi là giống nhau. Hai key major/minor tương đối như C major và A minor được chấp nhận, nhưng giữ key LM thay vì tự đổi tâm điệu; giao diện ghi chú rõ. Các trường hợp BPM nửa/gấp đôi hoặc khác key đáng kể vẫn cần xem lại.
- Kết quả LM gốc, phép đo, cảnh báo và hash audio được lưu trong nhãn; khi tạo dataset, bằng chứng đối chiếu đi kèm metadata, kể cả sau khi bản tải lên đã được dọn.

Trên Windows, `scripts/setup.bat` cài công cụ dòng lệnh chính thức vào `tools/essentia/`; không cần WSL/Docker hay thêm model GPU. Nếu cần cài riêng, chạy `.venv\Scripts\python.exe scripts\setup_essentia.py`. Bản tải được kiểm tra SHA-256. Có thể đặt `ESSENTIA_EXECUTABLE` tới công cụ tương thích khác; timeout mặc định 180 giây. `ESSENTIA_ENABLED=false` tắt đối chiếu và quay lại luồng LM cũ.

Nguồn và giới hạn của công cụ: [Essentia Music Extractor](https://essentia.upf.edu/streaming_extractor_music.html), [bản Windows chính thức](https://essentia.upf.edu/extractors/essentia-extractors-v2.1_beta5-356-g673b6a14-win-i686/), [giấy phép upstream](https://essentia.upf.edu/licensing_information.html).

## Tải và xóa model

Trong cửa sổ điều khiển, bấm **Quản lý model DiT / LM**. Danh sách lấy từ bản ACE-Step đã cài,
hiển thị loại model, tình trạng tải và dung lượng trên ổ đĩa.

1. Bấm **Dừng tất cả** trước khi tải hoặc xóa.
2. Chọn model, bấm **Tải / tiếp tục tải**. Có thể bấm **Hủy tải**; file tải dở được giữ để tải tiếp.
3. Khi xong, bấm **Khởi động**; model mới sẽ có trong phần chọn DiT/LM trên giao diện web.
4. Muốn giải phóng đĩa, chọn model đã tải và bấm **Xóa model đã chọn**, rồi xác nhận tên model.

Chỉ tải/xóa DiT và LM; ACE-Step, VAE, text encoder, nhạc, dataset và LoRA không bị xóa bởi mục này.
Model được chọn làm mặc định trong `.env` có thể được tải lại ở lần khởi động sau nếu bạn xóa nó.
Thanh tiến độ là trạng thái đang xử lý, kèm dung lượng đã ghi; không phải phần trăm tải chính xác.
Chi tiết tải nằm trong `data/logs/model-download.log`. Tải model không cập nhật hay thay phiên bản ACE-Step.

## Cài trên một máy khác

Bản này dùng hai thư mục cạnh nhau:

```text
ThuMucThuNghiem/
  ai_music_studio/       ứng dụng, .venv riêng và data
  ACE-Step-1.5/          engine, .venv riêng và checkpoints
```

Môi trường `.venv` phải cài lại trên máy mới; không chuyển nguyên môi trường từ máy khác.
Máy cần driver NVIDIA phù hợp, môi trường ACE-Step đã cài và model trong `checkpoints`.
Chạy `scripts/setup.bat` để cài Music Studio. Có thể tạo shortcut tới `Music Studio.bat` trên Desktop.
Nếu engine ở nơi khác, đặt `MUSIC_STUDIO_ENGINE_DIR` trong `.env` của Music Studio.
Sửa `ACESTEP_WORK_DIR` thành đường dẫn nằm trong thư mục engine trên máy mới.

DiT và LM trong `.env` của ACE-Step là lựa chọn gợi ý khi mở ứng dụng. Model chỉ được nạp sau khi admin bấm Áp dụng;
nếu chọn LM, hệ thống chờ LM sẵn sàng trước khi cho tạo nhạc. Lần đầu tải/nạp có thể lâu.
Bản điều khiển này chỉ quản lý các dịch vụ cục bộ và các tiến trình chính nó khởi động.
Nếu cổng bị chiếm bởi phiên khác, nó báo lỗi và không tự tắt ứng dụng bên ngoài.

## Chọn DiT, LM và LoRA

Mở thanh **Model**, chọn **DiT model** và **LM model** (hoặc **Không dùng**), rồi bấm
**Áp dụng và khởi động lại engine**. Đổi dropdown chỉ là chuẩn bị cấu hình, chưa thay model đang chạy.
Danh sách gồm các model đã tải đầy đủ trong `ACE-Step-1.5/checkpoints` khi mở ứng dụng.
Sau khi tải thêm model, mở lại ứng dụng để cập nhật danh sách.

Chỉ bấm Áp dụng khi không còn job đang chạy hoặc chờ. Nếu còn job, hệ thống từ chối và giữ nguyên engine;
admin đợi xong rồi bấm lại. Khi được chấp nhận, các yêu cầu job mới được khóa, engine cũ cùng tiến trình con
được dừng, rồi DiT/LM đã chọn được nạp. API và giao diện vẫn mở; nút Tạo nhạc bật lại khi engine và worker sẵn sàng.
Nếu nạp lỗi, chọn cấu hình khác (chẳng hạn LM nhỏ hơn) và bấm Áp dụng lại.

Dòng trạng thái hiển thị cấu hình thực tế đang chạy. LoRA chỉ được chọn từ các adapter tương thích
với DiT đang chạy, kèm độ mạnh LoRA. Đổi LoRA không khởi động lại engine.
Muốn dùng lại một bài tạo bằng model gốc khác, áp dụng DiT đó trước.

Lựa chọn qua giao diện chỉ áp dụng cho phiên hiện tại, không sửa `.env` hay xóa file model.
Lần mở ứng dụng tiếp theo dùng `ACESTEP_CONFIG_PATH`, `ACESTEP_INIT_LLM` và `ACESTEP_LM_MODEL_PATH`
trong `.env` của ACE-Step. Chức năng Áp dụng cần bộ điều khiển mở qua **Music Studio.bat**.

## Sao lưu và gỡ bỏ

- Dừng hệ thống, bấm **Sao lưu dữ liệu**, lưu ZIP bên ngoài hai thư mục ứng dụng.
- ZIP chứa database cùng nhạc, dataset và LoRA trong `data/storage`; không chứa cache hay model gốc.
- Để khôi phục: dừng hệ thống và giải nén thư mục `data` trong ZIP vào `ai_music_studio`.
- Muốn gỡ: dừng tất cả, giữ bản sao lưu nếu cần, rồi xóa hai thư mục thử nghiệm nêu trên.
- Trên máy hiện tại, `D:\ai_music_studio` và `D:\ACE-Step-1.5` vẫn giữ nguyên vị trí; không sao chép thêm model.
- Driver NVIDIA, Python đã cài và cache của công cụ cài đặt bên ngoài hai thư mục không bị gỡ tự động.

## Giới hạn bản thử nghiệm

Chưa phải bộ cài `.exe` độc lập; file BAT mở cửa sổ Windows bằng Python có sẵn trong `.venv`.
Chưa tự phục hồi sau khi Windows mất điện hoặc cửa sổ bị kết thúc cưỡng bức.
Nếu train xong VRAM vẫn cao, dừng toàn bộ rồi khởi động lại trước lượt train tiếp theo.
Không có thay đổi nào đối với model, dataset hay LoRA hiện có.
