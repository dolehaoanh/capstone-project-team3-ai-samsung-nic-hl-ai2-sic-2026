# Hướng dẫn chạy demo

Tài liệu vận hành cho ứng dụng web (`vietnam_stock_forecasting.api`), lane App
(Issue #1). Mọi lệnh dưới đây đã được chạy thực tế trên Windows + Python 3.11.

Ứng dụng chạy **hoàn toàn offline**: không cần internet, không cần API key. Dữ liệu
đến từ snapshot CSV đã commit trong repo.

## 1. Lập môi trường (một lần)

```bash
python -m venv .venv
.venv/Scripts/python.exe -m pip install -e ".[dev]"     # Windows
# source .venv/bin/activate && python -m pip install -e ".[dev]"   # macOS/Linux
```

Kiểm tra trước khi demo:

```bash
.venv/Scripts/python.exe -m pytest -q
```

Kết quả mong đợi: **56 passed**. Có một warning từ `starlette`
(`Using httpx with starlette.testclient is deprecated`) — đến từ thư viện, không phải
code dự án, bỏ qua được.

## 2. Chạy ứng dụng

```bash
.venv/Scripts/python.exe -m uvicorn vietnam_stock_forecasting.api:app --reload
```

| Địa chỉ | Nội dung |
|---|---|
| <http://127.0.0.1:8000/> | Dashboard tiếng Việt — dùng cái này để demo |
| <http://127.0.0.1:8000/docs> | Swagger UI, để demo API contract |
| <http://127.0.0.1:8000/health> | Kiểm tra sống/chết |

Đường dẫn snapshot **không phụ thuộc thư mục đang đứng**, nên chạy `uvicorn` từ đâu
cũng được.

## 3. Cấu hình bằng biến môi trường

Dự án **không có bộ nạp `.env`** (không có `python-dotenv`), nên `.env` sẽ không tự
được đọc. Phải export trong shell.

| Biến | Mặc định | Tác dụng |
|---|---|---|
| `SNAPSHOT_PATH` | — | Đường dẫn snapshot CSV; ưu tiên cao nhất |
| `DATA_DIR` | `data` | Dùng khi không có `SNAPSHOT_PATH`; đọc `<DATA_DIR>/sample/vn30_snapshot_example.csv` |
| `SNAPSHOT_STALE_DAYS` | `7` | Dữ liệu cũ hơn ngưỡng này sẽ sinh thông báo trong `warnings` |

Đường dẫn tương đối được giải theo **gốc repo**, không theo thư mục đang đứng.

```powershell
# PowerShell
$env:SNAPSHOT_PATH = "data/sample/vn30_snapshot_example.csv"
$env:SNAPSHOT_STALE_DAYS = "7"
```

```bash
# Git Bash / macOS / Linux
export SNAPSHOT_PATH=data/sample/vn30_snapshot_example.csv
export SNAPSHOT_STALE_DAYS=7
```

**Đổi snapshot không cần restart app.** Cache được khóa theo đường dẫn + `mtime` +
kích thước file, nên sau khi lane Data ghi file mới vào cùng đường dẫn, request kế
tiếp tự nạp lại. Chỉ cần restart khi đổi *biến môi trường*.

## 4. Kịch bản demo (khoảng 5 phút)

### Bước 1 — Mở dashboard và tải dự báo

Mở <http://127.0.0.1:8000/>, bấm **"Tải dự báo snapshot"**.

Với `vn30_snapshot_example.csv` (10 mã VN30, dữ liệu VCI, 2024-01-02 → 2024-06-28),
kết quả hiện tại:

| Mã | Xác suất tăng | Lợi nhuận kỳ vọng | Độ biến động | Độ tin cậy |
|---|---|---|---|---|
| FPT | 57,9% | +3,77% | 1,81% | 15,9% |
| TCB | 53,6% | +4,44% | 1,26% | 7,3% |
| MBB | 50,8% | +3,29% | 1,44% | 1,6% |
| … | … | … | … | … |
| VCB | 46,4% | +2,64% | 0,67% | 7,1% |

**Điểm cần nói:** xác suất quanh 0,5 và độ tin cậy thấp là kết quả *đáng mong đợi*
của dự báo 20 phiên, không phải dấu hiệu mô hình kém. Một công cụ trả về "95% chắc
chắn tăng" cho cổ phiếu mới là thứ đáng ngờ.

### Bước 2 — Chỉ ra provenance và thông báo tuổi dữ liệu

Dòng trạng thái ngay dưới nút hiển thị:

```
Tạo lúc: … | Chế độ: snapshot | Nguồn: vnstock:VCI | Dữ liệu: 2/1/24 → 28/6/24
```

Và một dải thông báo:

> Snapshot kết thúc ngày 28/06/2024, cũ **N** ngày. Đây là dữ liệu nghiên cứu offline,
> không phải dữ liệu thời gian thực.

`N` được tính theo ngày giờ Việt Nam tại thời điểm gọi, nên nó tăng mỗi hôm (khoảng
840 ngày vào giữa tháng 10/2026).

**Điểm cần nói:** nguồn dữ liệu, khoảng dữ liệu và tuổi dữ liệu luôn hiện cùng kết
quả. Công cụ không giả vờ dữ liệu là realtime.

### Bước 3 — Mở phần "Bằng chứng" của một mã

Bấm **"Xem"** ở cột cuối, ví dụ với FPT:

```
Động lượng 20 phiên: +12,3%
Động lượng 5 phiên: -4,1%
Biến động 20 phiên: 1,8%
Khối lượng so với trung bình 20 phiên: 0,87x
Khoảng cách so với MA20: +1,9%
```

**Điểm cần nói:** mỗi mã có bằng chứng riêng tính từ dữ liệu của chính nó. Đọc được
câu chuyện thật — FPT tăng 12,3% trong 20 phiên nhưng *giảm* 4,1% trong 5 phiên gần
nhất, khối lượng dưới trung bình. Người dùng tự đánh giá, không phải tin một con số.

### Bước 4 — Danh sách nghiên cứu theo mức rủi ro

Cuộn xuống phần **"Danh sách nghiên cứu theo mức rủi ro"**:

1. Thử bấm nút khi **chưa** đánh dấu xác nhận → nút bị khóa, có dòng giải thích.
2. Chọn mức rủi ro, đánh dấu xác nhận, bấm **"Tạo danh sách nghiên cứu"**.

Kết quả trên snapshot hiện tại:

| Mức rủi ro | Danh sách |
|---|---|
| Thận trọng | TCB, FPT, HPG |
| Cân bằng | TCB, FPT, HPG, BID, MBB |
| Tăng trưởng | TCB, FPT, BID, HPG, MBB |

**Điểm cần nói:** khuyến nghị chỉ chạy sau khi người dùng chọn mức rủi ro *và* xác
nhận. Cổng này có hai lớp: UI khóa nút, và backend trả `400` nếu ai gọi API trực tiếp
mà không xác nhận.

**Nếu bị hỏi "sao ba mức cho ra gần giống nhau?"** — trả lời thẳng: 10 mã này đều là
bluechip VN30 với độ biến động rất gần nhau (0,67%–1,81%), nên không có mã nào đủ
"đặc biệt thận trọng" hay "đặc biệt tăng trưởng" để tách ra. Cơ chế phân hóa đúng khi
ứng viên thật sự khác nhau — có unit test chứng minh. Với snapshot 30 mã dài hơn, khác
biệt sẽ rõ hơn. **Đừng nói đây là bug.**

### Bước 5 — Demo các trạng thái lỗi (phần dễ ăn điểm)

Đây là phần nhiều nhóm bỏ qua. Mở `/docs` hoặc dùng `curl`:

```bash
# Chế độ trực tuyến chưa bật
curl -i "http://127.0.0.1:8000/forecasts?mode=online"
# -> 501 "Chế độ làm mới trực tuyến chưa được bật trong MVP."

# Chưa xác nhận
curl -i -X POST http://127.0.0.1:8000/recommendations \
  -H "Content-Type: application/json" \
  -d '{"confirmed":false,"risk_profile":"balanced","symbols":["FPT"]}'
# -> 400

# Mã không có trong snapshot
curl -i -X POST http://127.0.0.1:8000/recommendations \
  -H "Content-Type: application/json" \
  -d '{"confirmed":true,"risk_profile":"balanced","symbols":["KHONGCO"]}'
# -> 422, nêu rõ mã nào bị bỏ
```

Và trạng thái snapshot hỏng — đặt `SNAPSHOT_PATH` sai rồi restart:

```powershell
$env:SNAPSHOT_PATH = "data/sample/khong-ton-tai.csv"
```

Dashboard hiện thông báo lỗi tiếng Việt, **không phải traceback**:

> Không đọc được snapshot dữ liệu. Kiểm tra đường dẫn snapshot và tài liệu chạy demo.

### Bước 6 — Demo cơ chế tự bảo vệ (tuỳ chọn, rất thuyết phục)

Trỏ về snapshot mẫu một mã rồi restart:

```powershell
$env:SNAPSHOT_PATH = "data/sample/vn30_sample.csv"
```

Ứng dụng vẫn trả `200` nhưng hiện **hai** thông báo:

> Snapshot kết thúc ngày 20/02/2024, cũ N ngày. Đây là dữ liệu nghiên cứu offline,
> không phải dữ liệu thời gian thực.

> Dữ liệu huấn luyện chỉ có một lớp kết quả: mọi phiên đều tăng sau 20 phiên. Mô hình
> không học được gì từ đó và chỉ trả về tỷ lệ nền, nên các con số dưới đây không mang
> thông tin dự báo. Không dùng để ra quyết định.

**Điểm cần nói:** snapshot này chỉ đi một chiều nên mọi nhãn huấn luyện đều giống
nhau; mô hình không có gì để học và chỉ trả về tỷ lệ nền, ở đây là 100%. Ứng dụng kiểm
tra số lớp trong tập huấn luyện và **tự thú nhận là nó không biết**, thay vì trưng con
số 100% ra như một dự báo tự tin. Đây là yêu cầu an toàn, không phải tính năng phụ.

Nhớ đặt lại `SNAPSHOT_PATH` về snapshot chính sau khi demo.

## 5. Xử lý sự cố

| Hiện tượng | Nguyên nhân | Cách xử lý |
|---|---|---|
| `python` không chạy / mở Microsoft Store | PATH trỏ vào app alias của Windows | Dùng trực tiếp `.venv/Scripts/python.exe` |
| `ModuleNotFoundError: fastapi` | Chạy bằng Python hệ thống, không phải venv | Dùng `.venv/Scripts/python.exe` |
| Dashboard báo "Không đọc được snapshot dữ liệu" | `SNAPSHOT_PATH` sai, file thiếu, hoặc CSV không hợp lệ | Kiểm tra đường dẫn; CSV phải đủ 7 cột và qua `validate_market_frame` |
| `422` "Snapshot không đủ lịch sử" | Mỗi mã cần ≥20 phiên để tính feature và thêm 20 phiên để có nhãn | Dùng snapshot ≥40 phiên/mã |
| Bảng hiện 1 dòng, mọi tỷ lệ bằng nhau | Đang dùng `vn30_sample.csv` (1 mã, dữ liệu tổng hợp) | Trỏ về `vn30_snapshot_example.csv` hoặc snapshot 30 mã |
| Đổi snapshot mà số không đổi | Cache theo `mtime` + kích thước | File mới phải thực sự khác; nếu vẫn vậy thì restart app |
| Cổng 8000 đã bị dùng | Tiến trình `uvicorn` cũ còn sống | Thêm `--port 8001` |

## 6. Giới hạn cần nói trước khi bị hỏi

Nói ra chủ động thì là sự trung thực khoa học; để giám khảo phát hiện thì là lỗ hổng.

1. **Mô hình huấn luyện in-sample ở endpoint này.** `/forecasts` fit trên toàn bộ lịch
   sử của snapshot rồi dự báo cho phiên mới nhất. Đánh giá out-of-sample nằm ở
   `walk_forward_evaluate` (lane Modeling, Issue #5), không phải ở số trên dashboard.
2. **Walk-forward chưa có embargo.** Target nhìn trước 20 phiên nhưng tập train nằm sát
   ngay tập test, nên hai tập chồng lấn khoảng forward. Thuộc Issue #5.
3. **Snapshot mẫu chỉ 10/30 mã và dừng ở 2024-06-28.** Xem
   [`CONTRACTS_VI.md` §1.2](CONTRACTS_VI.md) — bản 30 mã đang chờ lane Data.
4. **`volatility_20d` tính rolling xuyên ranh giới mã** với snapshot nhiều mã: khoảng 19
   dòng đầu của mã thứ hai trở đi dùng dữ liệu của mã liền trước. Thuộc Issue #3, đã
   ghi thành quy tắc ở `CONTRACTS_VI.md` §2.
5. **`target_positive` gán `0` cho phiên chưa biết tương lai** thay vì `NA`. Hiện chưa
   gây sai số vì các hàm train đều lọc thêm `forward_return`, nhưng là bẫy chờ sẵn.
   Xem `CONTRACTS_VI.md` §2.1, thuộc Issue #3.
6. **Chưa có so sánh với VN-Index, buy-and-hold hay lãi suất tiết kiệm** trên dashboard
   — ngoài phạm vi MVP, xem Q6 ở `CONTRACTS_VI.md` §6.
7. **Tiêu chí mức rủi ro là giá trị tạm** (`RISK_PROFILE_WEIGHTS` trong `api.py`), chờ
   lane Modeling chốt — Q4.
8. **Xếp hạng là tương đối, không có sàn.** Trên snapshot hiện tại, 7/10 mã có xác suất
   tăng dưới 50%, nên danh sách "thận trọng" vẫn chứa HPG ở 49,6%. Ứng dụng tự nêu điều
   này trong phần thông báo của danh sách — hãy đọc to câu đó khi demo, đừng để giám
   khảo là người đầu tiên chỉ ra. Việc có nên đặt sàn hay không thuộc Q4.
9. **Chế độ `online` chưa bật** (trả `501`), dù `fetch_vietnam_history` đã chạy được.
   Giữ offline để demo tái lập được — Q3.

## 7. Những câu không được nói

- Không nói đây là tư vấn đầu tư, hay nói công cụ "đảm bảo" bất kỳ mức lợi nhuận nào.
- Không diễn giải **độ tin cậy** là "xác suất đúng" hay "xác suất sinh lời". Nó đo mức
  mô hình lệch khỏi 50/50, không nói gì về việc có lãi hay không.
- Không gọi `vn30_snapshot_example.csv` là dữ liệu đầy đủ của VN30 — nó là 10/30 mã.
- Không trình bày số trên dashboard như kết quả backtest. Đó là hai thứ khác nhau.

Ràng buộc đầy đủ về cách diễn giải nằm ở [`CONTRACTS_VI.md` §3.1](CONTRACTS_VI.md).

## 8. Tài liệu liên quan

- Hợp đồng data/feature/model/API: [`CONTRACTS_VI.md`](CONTRACTS_VI.md)
- Pipeline dữ liệu thị trường: [`DATA_PIPELINE.md`](DATA_PIPELINE.md)
- Phân công và quy trình: [`TEAM_BRIEFING_VI.md`](TEAM_BRIEFING_VI.md),
  [`../CONTRIBUTING.md`](../CONTRIBUTING.md)
