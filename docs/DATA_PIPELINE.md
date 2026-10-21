# Hướng dẫn Pipeline Dữ liệu Thị trường (Market Data Pipeline)

Tài liệu hướng dẫn kỹ thuật cho module dữ liệu thị trường (`vietnam_stock_forecasting.data`), thực hiện bởi thành viên **Lưu Huy Wvu** (Issue #4).

## 1. Mục tiêu và Nguyên tắc cốt lõi

- **Online-first khi chạy ứng dụng:** Ứng dụng thử lấy dữ liệu mới theo mặc định và giữ snapshot local hợp lệ gần nhất làm fallback; huấn luyện và kiểm thử vẫn hoạt động hoàn toàn offline với snapshot đã xác thực.
- **Online Refresh tường minh:** Chỉ tải dữ liệu online khi người dùng hoặc developer chủ động kích hoạt qua lệnh CLI hoặc hàm `fetch_vietnam_history`.
- **Bảo toàn Provenance & Metadata:** Mỗi snapshot dữ liệu đều được lưu kèm metadata (nguồn, thời điểm lấy, khoảng thời gian, danh sách mã cổ phiếu và số lượng dòng) để phục vụ tái lập nghiên cứu.
- **Không Look-Ahead Leakage:** Dữ liệu lịch sử OHLCV được chuẩn hóa theo từng mốc thời gian point-in-time.

---

## 2. Hợp đồng dữ liệu (Data Contract)

Dữ liệu thị trường sau khi ingest được chuẩn hóa thành DataFrame với đúng 7 cột theo thứ tự:

| Cột | Kiểu dữ liệu | Mô tả |
| :--- | :--- | :--- |
| `date` | `datetime64[ns]` | Ngày giao dịch (chuẩn hóa `YYYY-MM-DD`, múi giờ Việt Nam UTC+7) |
| `symbol` | `str` | Mã cổ phiếu (viết hoa, không chứa khoảng trắng) |
| `open` | `float` | Giá mở cửa |
| `high` | `float` | Giá cao nhất trong ngày |
| `low` | `float` | Giá thấp nhất trong ngày |
| `close` | `float` | Giá đóng cửa |
| `volume` | `float` hoặc `int` | Khối lượng giao dịch |

### Quy tắc kiểm tra hợp lệ (Data Validation)

Hàm `validate_market_frame(frame)` kiểm soát chặt chẽ các lỗi:
1. **Thiếu cột:** Báo lỗi nếu thiếu bất kỳ cột nào trong 7 cột bắt buộc.
2. **Dữ liệu rỗng:** Không chấp nhận DataFrame rỗng hoặc chứa giá trị `NaN`/`None` ở bất kỳ cột nào.
3. **Ngày không hợp lệ:** Giá trị ngày phải parse được thành datetime hợp lệ.
4. **Mã cổ phiếu rỗng:** Ký tự mã không được để trống hoặc chỉ có khoảng trắng.
5. **Giá/Khối lượng âm:** Không cho phép giá hoặc khối lượng < 0.
6. **Mâu thuẫn giá:** `high` không được nhỏ hơn `low`.
7. **Trùng lặp:** Không cho phép trùng lặp cặp `(symbol, date)`.
8. **Ngày tương lai:** Không chấp nhận dữ liệu có ngày vượt quá ngày hiện tại (`today`).

---

## 3. Metadata Snapshot

Khi lưu snapshot bằng `write_csv_snapshot(...)`, hệ thống tự động tạo file sidecar `<tên_file>.metadata.json`.

Ví dụ nội dung file metadata:
```json
{
  "source": "vnstock:VCI",
  "retrieved_at": "2026-10-16T07:11:43.763456+00:00",
  "start_date": "2025-10-16",
  "end_date": "2026-10-16",
  "symbols": [
    "ACB",
    "BCM",
    "BID",
    "BVH",
    "CTG",
    "FPT",
    "GAS",
    "GVR",
    "HDB",
    "HPG",
    "MBB",
    "MSN",
    "MWG",
    "PLX",
    "POW",
    "SAB",
    "SHB",
    "SSB",
    "SSI",
    "STB",
    "TCB",
    "TPB",
    "VCB",
    "VHM",
    "VIB",
    "VIC",
    "VJC",
    "VNM",
    "VPB"
  ],
  "row_count": 7470,
  "frequency": "1D",
  "adjusted": true
}
```

Khi đọc snapshot bằng `load_csv_snapshot(...)`, thông tin metadata sẽ được tự động khôi phục
vào đối tượng `MarketSnapshot`. Nếu sidecar JSON hỏng hoặc có `retrieved_at`, ngày hay
`symbols` sai kiểu, loader báo `ValueError`; API chuyển lỗi này thành HTTP `503` thay vì
phục vụ CSV như một snapshot hợp lệ nhưng mất provenance.

---

## 4. Hướng dẫn sử dụng

### 4.1. Cài đặt môi trường

Cài đặt các gói dev:
```bash
python -m pip install -e ".[dev]"
```

Cài đặt thư viện lấy dữ liệu VNStock (tương thích vnstock v5 và v3):
```bash
python -m pip install --extra-index-url https://vnstocks.com/api/packages vnstock
```

### 4.2. Lấy dữ liệu và tạo Snapshot qua CLI

Module hỗ trợ giao diện dòng lệnh:

```bash
# Tải snapshot nhóm VN30 trong khoảng thời gian cụ thể:
python -m vietnam_stock_forecasting.data --symbols VN30 --start 2024-01-01 --end 2024-06-30 --output data/raw/vn30_snapshot.csv

# Tải snapshot cho danh sách mã tuỳ chọn:
python -m vietnam_stock_forecasting.data --symbols FPT,VCB,HPG --start 2024-01-01 --end 2024-06-30 --output data/raw/custom_snapshot.csv
```

### 4.3. Sử dụng trong code Python

```python
from vietnam_stock_forecasting.data import (
    load_csv_snapshot,
    write_csv_snapshot,
    fetch_vietnam_history,
    get_vn30_symbols,
)

# 1. Đọc snapshot offline kèm metadata
snapshot = load_csv_snapshot("data/raw/vn30_snapshot.csv")
print(snapshot.symbols)
print(snapshot.start_date, "đến", snapshot.end_date)
print(snapshot.frame.head())

# 2. Lấy dữ liệu online mới (khi cần)
symbols = get_vn30_symbols()
new_snapshot = fetch_vietnam_history(
    symbols=["FPT", "VCB"],
    start="2024-01-01",
    end="2024-03-31",
    source="VCI",
)

# 3. Lưu snapshot và metadata sidecar
write_csv_snapshot(new_snapshot, "data/raw/my_snapshot.csv")
```

### 4.4. Mô hình Cache-First + Cập nhật ngầm 1 lần/ngày (Khuyến nghị cho Web App)

Để tối ưu trải nghiệm người dùng không phải chờ đợi lâu và không bị nghẽn API rate limit (10 lượt/phút của vnstock v5):

1. **Phục vụ ngay từ snapshot cục bộ:** App đọc file `data/sample/vn30_snapshot.csv` có sẵn (~0.05s).
2. **Kiểm tra độ mới:** Dùng hàm `is_snapshot_stale(snapshot, max_age_days=1)`.
3. **Cập nhật ngầm trong nền (Background Tasks):** Nếu dữ liệu đã cũ (hơn 1 ngày kể từ lần fetch trước), ứng dụng kích hoạt một tác vụ chạy ngầm (`FastAPI BackgroundTasks` hoặc worker) để kéo dữ liệu mới và cập nhật lại file snapshot, không làm block giao diện người dùng.

```python
from fastapi import BackgroundTasks, FastAPI
from vietnam_stock_forecasting.data import is_snapshot_stale, load_csv_snapshot

app = FastAPI()
SNAPSHOT_PATH = "data/sample/vn30_snapshot.csv"

@app.get("/market-data")
async def get_market_data(background_tasks: BackgroundTasks):
    snapshot = load_csv_snapshot(SNAPSHOT_PATH)
    if is_snapshot_stale(snapshot, max_age_days=1):
        background_tasks.add_task(refresh_market_data_background, SNAPSHOT_PATH)
    return {"status": "ok", "retrieved_at": snapshot.retrieved_at, "data": snapshot.frame.to_dict(orient="records")}
```

---

## 5. Kiểm thử (Unit Tests)

Bộ test được thiết kế để chạy hoàn toàn **offline**, sử dụng mock và fixture, không phụ thuộc vào kết nối mạng:

```bash
python -m pytest tests/test_data.py -v
```

Kiểm tra toàn bộ test suite và định dạng git diff:
```bash
python -m pytest -q
git diff --check
```
