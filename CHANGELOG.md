# Lịch sử phiên bản

Mỗi bản phát hành có một mục `## <phiên bản>` (trùng file `VERSION`). Ghi theo góc nhìn admin:
có gì mới, có gì đổi cách dùng, có cần thêm biến nào vào `.env` hay không.

## 1.0.0

Bản đầu tiên giao cho máy admin.

- Tạo nhạc (Tạo mới, Remix, Sửa đoạn), Thư viện, Gán nhãn AI + đối chiếu Essentia, Dataset, Finetune LoRA, Hệ thống.
- Cập nhật online: cửa sổ điều khiển tự báo khi có bản mới và cập nhật bằng một nút (tự sao lưu, tự khôi phục
  khi lỗi). Vẫn cập nhật thủ công được bằng `Cap nhat Music Studio.bat`; `Khoi phuc ban truoc.bat` để quay lại bản trước.
- Số phiên bản hiện ở thanh bên giao diện, tab Hệ thống và tiêu đề cửa sổ điều khiển.
- Thư viện Python được khóa phiên bản trong `requirements.lock`.
- Cấu hình `.env`: `UPDATE_URL` (tùy chọn) — chỉ cần khi muốn đổi địa chỉ kênh cập nhật ghi sẵn trong gói.
