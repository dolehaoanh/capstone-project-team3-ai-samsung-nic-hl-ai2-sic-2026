# Quy trình làm việc nhóm

## Nguyên tắc chung

- Mỗi thành viên làm việc trên **branch riêng**, không sửa trực tiếp `main`.
- Mỗi nhiệm vụ phải có một GitHub Issue và mỗi thay đổi phải đi qua Pull Request.
- Không đưa API key, token, dữ liệu cá nhân hoặc dữ liệu chưa rõ giấy phép vào repository.
- Không dùng dữ liệu tương lai để tạo feature hoặc đánh giá mô hình.
- Mọi PR phải chạy `python -m pytest -q` và `git diff --check` trước khi gửi.

## Quy ước branch

Đặt tên branch theo mẫu:

```text
<member>/<work-item>
```

Ví dụ: `luu-huy-wvu/market-data-connector`.

## Quy trình Pull Request

1. Gắn PR với Issue bằng `Closes #<số-issue>`.
2. Mô tả rõ thay đổi, hợp đồng dữ liệu/API bị ảnh hưởng, và lệnh kiểm thử.
3. Ít nhất một thành viên khác review.
4. Hao Anh là người phê duyệt cuối và merge vào `main`.
5. Không merge nếu CI thất bại hoặc còn xung đột hợp đồng giữa các lane.

## Hợp đồng tích hợp

- Dữ liệu thị trường phải có: `date`, `symbol`, `open`, `high`, `low`, `close`, `volume`.
- Dự báo phải giữ các trường trong `Forecast` ở `src/vietnam_stock_forecasting/contracts.py`.
- Thay đổi schema phải được ghi trong PR và cập nhật test tương ứng.
- Chế độ offline là mặc định; online refresh phải là tính năng tùy chọn và có provenance.

## Kiểm thử tối thiểu

```bash
python -m pip install -e ".[dev]"
python -m pytest -q
git diff --check
```
