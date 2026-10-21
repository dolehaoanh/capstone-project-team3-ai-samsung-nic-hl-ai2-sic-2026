# Hướng dẫn chạy demo

Tài liệu vận hành cho ứng dụng web (`vietnam_stock_forecasting.api`), lane App
(Issue #1). Mọi lệnh dưới đây đã được chạy thực tế trên Windows + Python 3.11.

Mặc định ứng dụng **ưu tiên lấy dữ liệu trực tuyến**. Ứng dụng vẫn trả lời ngay bằng
snapshot hợp lệ mới nhất trên đĩa trong lúc cập nhật chạy nền; nếu mất mạng hoặc provider
thất bại, snapshot local đó tiếp tục được dùng. Có thể ép chạy offline bằng cách đặt
`ALLOW_ONLINE_REFRESH=false` — xem [§3.1](#31-cập-nhật-dữ-liệu-ngầm).

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

Kết quả mong đợi: **114 passed** (93 test của lane App trong `tests/test_api.py`,
`tests/test_refresh.py` và `tests/test_dashboard.py`, còn lại của các lane Data, Features,
Modeling). Không test nào gọi mạng: hàm lấy dữ liệu được thay bằng bản giả trong mọi test
cập nhật ngầm.

`tests/test_dashboard.py` chạy đúng đoạn JavaScript của dashboard bằng **Node.js** (không
cần `npm install`), trên DOM giả, với response thật của API: bảng xếp hạng, tuổi dữ liệu
theo ngày Việt Nam, thông báo lỗi `422`/`503`, chống chèn HTML, và khóa nút danh
sách nghiên cứu cho đến khi đã tải dự báo và đánh dấu xác nhận. Máy không có Node thì 20
test này được **bỏ qua** (kết quả `94 passed, 20 skipped`); trên CI (`CI=true`) chúng
**fail** thay vì bỏ qua. Có một warning từ `starlette`
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
| `DATA_DIR` | `data` | Dùng khi không có `SNAPSHOT_PATH`; đọc `<DATA_DIR>/sample/vn30_snapshot.csv` |
| `SNAPSHOT_STALE_DAYS` | `7` | Dữ liệu cũ hơn ngưỡng này sẽ sinh thông báo trong `warnings` |
| `ALLOW_ONLINE_REFRESH` | bật | `false` để tắt cập nhật trực tuyến và chạy offline (§3.1) |
| `LIVE_SNAPSHOT_PATH` | `<DATA_DIR>/raw/vn30_live.csv` | File mà lần cập nhật ngầm ghi ra (git bỏ qua thư mục này) |
| `REFRESH_HISTORY_DAYS` | `365` | Số ngày lịch sử mỗi lần cập nhật kéo về, bằng độ dài snapshot gốc |
| `REFRESH_RETRY_MINUTES` | `30` | Thời gian chờ trước khi thử lại sau một lần cập nhật lỗi |

Đường dẫn tương đối được giải theo **gốc repo**, không theo thư mục đang đứng.

```powershell
# PowerShell
$env:SNAPSHOT_PATH = "data/sample/vn30_snapshot.csv"
$env:SNAPSHOT_STALE_DAYS = "7"
```

```bash
# Git Bash / macOS / Linux
export SNAPSHOT_PATH=data/sample/vn30_snapshot.csv
export SNAPSHOT_STALE_DAYS=7
```

**Đổi snapshot không cần restart app.** Cache được khóa theo đường dẫn + `mtime` +
kích thước file CSV, cộng `mtime` của file metadata đi kèm, nên sau khi lane Data ghi
file mới vào cùng đường dẫn (kể cả khi chỉ sửa metadata), request kế tiếp tự nạp lại.
Chỉ cần restart khi đổi *biến môi trường*.

### 3.1. Cập nhật dữ liệu ngầm (mặc định)

App trả lời ngay từ snapshot có sẵn, còn việc kéo dữ liệu mới chạy ngầm, không bắt người
dùng chờ. Đây là mặc định. Không cần đặt biến môi trường nếu máy có mạng và đã cài
`vnstock`. Để ép offline:

```powershell
$env:ALLOW_ONLINE_REFRESH = "false"
.venv\Scripts\python.exe -m uvicorn vietnam_stock_forecasting.api:app
```

Để cài provider khi cần:

```powershell
.venv\Scripts\python.exe -m pip install --extra-index-url https://vnstocks.com/api/packages vnstock
```

Cách hoạt động:

1. Mỗi request `/forecasts` hoặc `/recommendations` trả lời **ngay** từ file trên đĩa.
2. App kiểm tra snapshot đang dùng đã có phiên đóng cửa gần nhất chưa. Phiên được coi là
   đóng sau **15:00 giờ VN** của ngày thường (ATC kết thúc 14:45). Điều kiện xét là
   `retrieved_at` có trước 15:00 của phiên đó hay không, **không** so `end_date` với
   lịch: so với lịch thì thứ 7, CN và ngày lễ lúc nào cũng "cũ", và mỗi request sẽ kích
   hoạt một lần gọi sàn.
3. Nếu thiếu, một luồng nền gọi `fetch_vietnam_history` của lane Data cho đúng các mã
   trong snapshot, lấy 365 ngày đến phiên đó, bỏ mọi dòng sau phiên đó (giá giữa phiên).
   Với 30 mã, mỗi lần mất khoảng 3–4 phút vì lane Data giãn cách 6,5 giây giữa các mã.
4. Kết quả được ghi **nguyên khối** ra `data/raw/vn30_live.csv` + metadata: ghi ra thư mục
   tạm cạnh file đích, đọc kiểm lại, rồi mới thay. Người đang xem không bao giờ đọc phải
   file ghi dở. File `data/sample/vn30_snapshot.csv` mà git theo dõi **không bao giờ bị ghi
   đè**, nên repo không bị bẩn và dataset nộp bài không đổi.
5. Request sau tự dùng file live nếu nó hợp lệ và mới hơn snapshot gốc. File live hỏng
   thì quay về snapshot gốc.

Trong lúc chạy và khi lỗi, dashboard hiện thông báo trong dải cảnh báo, ví dụ *"Đang cập
nhật dữ liệu thị trường mới trong nền… tải lại sau vài phút"*. Chỉ **một** lần cập nhật
chạy tại một thời điểm, kể cả khi chạy nhiều worker (có file khóa `vn30_live.lock`). Lỗi
thì chờ `REFRESH_RETRY_MINUTES` mới thử lại. Một phiên đã lấy thành công thì không lấy
lại.

Trạng thái chi tiết (bật/tắt, đang chạy, lần cuối, lỗi kỹ thuật) xem ở
<http://127.0.0.1:8000/health>, trong khóa `snapshot_refresh`.

Nếu provider hoặc mạng không khả dụng, app **bỏ qua lỗi và tiếp tục dùng** snapshot local
hợp lệ gần nhất; lỗi được hiển thị trong `warnings` và trạng thái `/health`. Muốn demo
đóng băng số liệu, đặt `ALLOW_ONLINE_REFRESH=false` trước khi khởi động app.

## 4. Kịch bản demo (khoảng 5 phút)

### Bước 1 — Mở dashboard và tải dự báo

Mở <http://127.0.0.1:8000/>, bấm **"Tải dự báo mới nhất"**.

Với snapshot mặc định `vn30_snapshot.csv` (30 mã VN30, dữ liệu VCI, 249 phiên/mã,
16/10/2025 → 16/10/2026), kết quả tại thời điểm viết:

| Mã | Xác suất tăng | Lợi nhuận kỳ vọng | Độ biến động | Độ tin cậy |
|---|---|---|---|---|
| PNJ | 73,6% | +3,37% | 4,39% | 47,2% |
| SSB | 66,6% | +3,78% | 2,82% | 33,3% |
| STB | 59,3% | +2,21% | 1,51% | 18,7% |
| … | … | … | … | … |
| VPB | 44,0% | −1,08% | 1,51% | 12,0% |

Con số sẽ đổi khi lane Data làm mới snapshot — đọc lại bảng ngay trước buổi demo,
đừng học thuộc bảng trên.

**Điểm cần nói:** phần lớn xác suất nằm quanh 0,5 và độ tin cậy thấp là kết quả *đáng
mong đợi* của dự báo 20 phiên, không phải dấu hiệu mô hình kém. 10/30 mã dưới 50%.
Một công cụ trả về "95% chắc chắn tăng" cho mọi cổ phiếu mới là thứ đáng ngờ.

**Chuẩn bị sẵn câu trả lời cho mã đứng đầu** — xem Bước 3.

### Bước 2 — Chỉ ra provenance và thông báo tuổi dữ liệu

Dòng trạng thái ngay dưới nút hiển thị:

```
Tạo lúc: … | Chế độ: snapshot | Nguồn: vnstock:VCI | Dữ liệu: 16/10/25 → 16/10/26 (dữ liệu cách đây N ngày)
```

Tuổi dữ liệu **luôn** hiện trong dòng này, tính theo ngày giờ Việt Nam, kể cả khi chưa
vượt ngưỡng cảnh báo.

Với snapshot hiện tại, ngay dưới dòng trạng thái có một dải thông báo:

> Phiên 16/10/2026 được lấy lúc 14:02 giờ Việt Nam, trước giờ đóng cửa (15:00), nên giá
> của phiên này có thể là giá giữa phiên, chưa phải giá chốt.

Đây là giới hạn 4 ở §6, giờ app tự phát hiện từ `retrieved_at` thay vì để người trình
bày phải nhớ. Dải này biến mất khi lane Data lấy lại file sau giờ đóng cửa, hoặc khi
cập nhật ngầm (§3.1) đã ghi file live.

Snapshot còn mới (trong ngưỡng `SNAPSHOT_STALE_DAYS`, mặc định 7 ngày) nên **không**
có dải cảnh báo tuổi dữ liệu. Nếu demo diễn ra hơn 7 ngày sau ngày dữ liệu cuối mà
snapshot chưa được làm mới, dải này sẽ xuất hiện:

> Snapshot kết thúc ngày 16/10/2026, cũ **N** ngày. Đây là dữ liệu nghiên cứu offline,
> không phải dữ liệu thời gian thực.

`N` được tính theo ngày giờ Việt Nam tại thời điểm gọi. Muốn trình diễn dải này mà
không chờ, đặt `SNAPSHOT_STALE_DAYS=0` rồi restart.

**Điểm cần nói:** nguồn dữ liệu và khoảng dữ liệu luôn hiện cùng kết quả, và hệ thống
tự cảnh báo khi dữ liệu cũ. Công cụ không giả vờ dữ liệu là realtime.

### Bước 3 — Mở phần "Bằng chứng" của một mã

Bấm **"Xem"** ở cột cuối của mã đứng đầu, PNJ:

```
Động lượng 20 phiên: -48,8%
Động lượng 5 phiên: -3,6%
Biến động 20 phiên: 4,4%
Khối lượng so với trung bình 20 phiên: 0,37x
Khoảng cách so với MA20: -24,7%
```

Đây là câu giám khảo **chắc chắn** hỏi: *"Mã vừa mất gần nửa giá trị trong một tháng
lại đứng số 1?"* Trả lời theo đúng dữ liệu:

- **Đây là biến động thật, không phải lỗi dữ liệu.** Từ 28/09 đến 01/10/2026, PNJ có
  4 phiên liền `open = high = low = close`, mỗi phiên −7% — tức là giảm sàn, giá đi từ
  ~38 về ~18 nghìn đồng. Không có phiên nào vượt biên độ ±7% của HOSE, nên đây không
  phải chia tách cổ phiếu chưa điều chỉnh.
- **Con số đến từ một biến duy nhất.** Trong mô hình logistic, `return_20d` có hệ số
  lớn nhất và **âm** (−0,23 trên thang chuẩn hóa; các feature khác gần 0): mô hình học
  được rằng giảm mạnh trong 20 phiên thì dễ hồi lại. PNJ giảm 48,8% nên nhận xác suất
  cao nhất. `distance_from_ma20` gần như không đóng góp gì.
- **Bằng chứng cho quy luật đó rất mỏng — hãy nói thẳng.** Trong tập huấn luyện chỉ có
  38 dòng giảm hơn 30% trong 20 phiên, thuộc 7 mã, và **17 dòng trong đó là chính PNJ**
  trong đợt giảm tháng 7–8/2026. Nhóm này có 50% tăng sau 20 phiên, so với tỷ lệ nền
  45,6%. Tức là xác suất 73,6% chủ yếu là mô hình tuyến tính **ngoại suy** từ một nhóm
  nhỏ, không phải tín hiệu vững. Đây là giới hạn của baseline, **không phải lời khuyên
  bắt đáy**.
- **Độ biến động 4,4% là cao nhất rổ** (các mã ngân hàng trung bình 1,4%), và đó là lý
  do danh sách "thận trọng" ở Bước 4 **không** chọn PNJ dù nó đứng đầu về xác suất.

**Điểm cần nói:** mỗi mã có bằng chứng riêng tính từ dữ liệu của chính nó. Người dùng
thấy được *vì sao* một mã được xếp hạng cao và tự đánh giá, thay vì tin một con số.

### Bước 4 — Danh sách nghiên cứu theo mức rủi ro

Cuộn xuống phần **"Danh sách nghiên cứu theo mức rủi ro"**:

1. Thử bấm nút khi **chưa** đánh dấu xác nhận → nút bị khóa, có dòng giải thích.
2. Chọn mức rủi ro, đánh dấu xác nhận, bấm **"Tạo danh sách nghiên cứu"**.

Kết quả trên snapshot hiện tại:

| Mức rủi ro | Danh sách |
|---|---|
| Thận trọng | SSB, STB, VCB |
| Cân bằng | SSB, PNJ, STB, VCB, FPT |
| Tăng trưởng | SSB, PNJ, STB, SSI, FPT |

**Điểm cần nói:** khuyến nghị chỉ chạy sau khi người dùng chọn mức rủi ro *và* xác
nhận. Cổng này có hai lớp: UI khóa nút, và backend trả `400` nếu ai gọi API trực tiếp
mà không xác nhận.

**Chỉ ra sự phân hóa giữa ba mức:** PNJ đứng đầu về xác suất (73,6%) nhưng biến động
cao nhất rổ (4,39%), nên bị loại khỏi danh sách "thận trọng" và chỉ xuất hiện ở "cân
bằng" và "tăng trưởng". Ngược lại VCB (52,1%) vào được "thận trọng" nhờ biến động thấp.
Độ biến động trong rổ trải từ 0,60% đến 4,39%, đủ rộng để trọng số của từng mức tạo ra
khác biệt thật.

Danh sách mỗi lần tạo đi kèm phần thông báo nêu số mã được chọn, ngày dữ liệu, nguồn và
kỳ hạn. Nếu danh sách chứa mã có xác suất tăng dưới 50% thì thông báo nói rõ điều đó —
trên snapshot hiện tại cả ba mã "thận trọng" đều trên 50% nên câu này không xuất hiện.

### Bước 5 — Demo các trạng thái lỗi (phần dễ ăn điểm)

Đây là phần nhiều nhóm bỏ qua. Mở `/docs` hoặc dùng `curl`:

```bash
# Chế độ trực tuyến (mặc định, có fallback local)
curl -i "http://127.0.0.1:8000/forecasts?mode=online"
# -> 200; nếu refresh thất bại, response vẫn dùng snapshot local gần nhất

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

Ứng dụng vẫn trả `200` nhưng hiện **ba** thông báo:

> Snapshot kết thúc ngày 20/02/2024, cũ N ngày. Đây là dữ liệu nghiên cứu offline,
> không phải dữ liệu thời gian thực.

> Phiên 20/02/2024 được lấy lúc 07:00 giờ Việt Nam, trước giờ đóng cửa (15:00), …

(metadata của file mẫu ghi `retrieved_at` là 00:00 UTC ngày dữ liệu cuối, nên app đọc
đúng những gì metadata khai báo)

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
| Bảng hiện 1 dòng, mọi tỷ lệ bằng nhau | Đang dùng `vn30_sample.csv` (1 mã, dữ liệu tổng hợp) | Bỏ `SNAPSHOT_PATH` hoặc trỏ về `data/sample/vn30_snapshot.csv` |
| Đổi snapshot mà số không đổi | Cache theo `mtime` + kích thước | File mới phải thực sự khác; nếu vẫn vậy thì restart app |
| Cổng 8000 đã bị dùng | Tiến trình `uvicorn` cũ còn sống | Thêm `--port 8001` |
| Dải "Lần cập nhật dữ liệu gần nhất … không thành công" | Chưa cài `vnstock`, mất mạng, hoặc sàn chặn rate-limit | Xem `last_error` ở `/health`; app vẫn chạy bằng snapshot cũ và tự thử lại sau `REFRESH_RETRY_MINUTES` phút |
| Bật cập nhật ngầm mà không thấy chạy | Snapshot đã lấy sau 15:00 của phiên gần nhất, hoặc đang chờ thử lại sau lỗi | Xem `target_session`, `last_outcome` ở `/health`; đây là hành vi đúng |
| Cập nhật ngầm không bao giờ chạy lại sau khi server bị tắt đột ngột | File khóa `vn30_live.lock` còn sót | App tự bỏ file khóa cũ hơn 1 giờ; muốn ngay thì xóa tay |

## 6. Giới hạn cần nói trước khi bị hỏi

Nói ra chủ động thì là sự trung thực khoa học; để giám khảo phát hiện thì là lỗ hổng.

1. **Mô hình huấn luyện in-sample ở endpoint này.** `/forecasts` fit trên toàn bộ lịch
   sử của snapshot rồi dự báo cho phiên mới nhất. Đánh giá out-of-sample nằm ở
   `walk_forward_evaluate` (lane Modeling, Issue #5), không phải ở số trên dashboard.
2. **Walk-forward chưa có embargo.** Target nhìn trước 20 phiên nhưng tập train nằm sát
   ngay tập test, nên hai tập chồng lấn khoảng forward. Thuộc Issue #5.
3. **Snapshot chỉ có 1 năm lịch sử** (249 phiên/mã). Sau khi trừ 20 phiên warmup và
   20 phiên cuối chưa có nhãn, mỗi mã còn khoảng 209 dòng huấn luyện — đủ cho demo,
   mỏng cho backtest. Xem [`CONTRACTS_VI.md` §1.2](CONTRACTS_VI.md).
4. **Phiên cuối của snapshot có thể chưa đóng cửa.** Snapshot hiện tại được lấy lúc
   14:02 giờ VN ngày 16/10/2026 (`retrieved_at` 07:02 UTC), trước giờ HOSE đóng cửa
   (ATC kết thúc 14:45). Dòng ngày 16/10 của cả 30 mã vì vậy là giá giữa phiên, không
   phải giá đóng cửa. File thuộc lane Data (Issue #4); app hiện tự cảnh báo điều này
   trên dashboard (Bước 2), và cập nhật ngầm chỉ ghi các phiên đã đóng cửa.
5. **Mô hình gần như không có khả năng dự báo ngoài mẫu trên snapshot hiện tại.** Chạy
   `walk_forward_evaluate` trên 30 mã: tham số mặc định cho **AUC 0,534, accuracy
   50,9%**; với `min_train_rows=1000, test_window=300` thì AUC 0,484, accuracy 47,8%.
   Tức là xấp xỉ đoán ngẫu nhiên, dù walk-forward còn thiếu embargo (giới hạn 2) nên lẽ
   ra kết quả còn được "ưu ái". Các con số trên dashboard là đầu ra của baseline,
   **không phải tín hiệu đã được kiểm chứng**. Nếu bị hỏi "mô hình chính xác bao nhiêu",
   đây là câu trả lời thật; đừng đưa accuracy in-sample.
   Các chỉ số lợi nhuận của hàm này **không được trích dẫn** cho tới khi lane Modeling
   (Issue #5) sửa: chúng nhân dồn lợi nhuận 20 phiên chồng lấn nhau của mọi mã, nên với
   tham số mặc định `strategy_return` ra **+438.270%** trong khi `buy_and_hold_return`
   ra −100%. Lợi nhuận mua và giữ thực tế của rổ trong năm có trung vị −8,4%.
6. **`target_positive` gán `0` cho phiên chưa biết tương lai** thay vì `NA`. Hiện chưa
   gây sai số vì các hàm train đều lọc thêm `forward_return`, nhưng là bẫy chờ sẵn.
   Xem `CONTRACTS_VI.md` §2.1, thuộc Issue #3.
7. **Chưa có so sánh với VN-Index, buy-and-hold hay lãi suất tiết kiệm** trên dashboard
   — ngoài phạm vi MVP, xem Q6 ở `CONTRACTS_VI.md` §6.
8. **Tiêu chí mức rủi ro là giá trị tạm** (`RISK_PROFILE_WEIGHTS` trong `api.py`), chờ
   lane Modeling chốt — Q4.
9. **Xếp hạng là tương đối, không có sàn.** Shortlist chọn mã tốt nhất *trong tập ứng
   viên*, kể cả khi phần lớn tập đó có xác suất tăng dưới 50% (hiện 10/30 mã). Khi danh
   sách chứa mã dưới 50%, ứng dụng tự nêu trong phần thông báo — hãy đọc to câu đó khi
   demo. Việc có nên đặt sàn hay không thuộc Q4.
10. **Chế độ `online` là mặc định.** Dữ liệu mới nhất từ sàn được cập nhật ngầm
   (§3.1); trong lúc chờ hoặc khi lỗi, response vẫn dùng snapshot local gần nhất.
   Cập nhật ngầm **bật mặc định**, và nếu một mã trả về rỗng thì cả lần cập nhật 30 mã
   thất bại (lỗi bên `fetch_vietnam_history`, Issue #4) — app báo lỗi rồi thử lại sau.

## 7. Những câu không được nói

- Không nói đây là tư vấn đầu tư, hay nói công cụ "đảm bảo" bất kỳ mức lợi nhuận nào.
- Không diễn giải **độ tin cậy** là "xác suất đúng" hay "xác suất sinh lời". Nó đo mức
  mô hình lệch khỏi 50/50, không nói gì về việc có lãi hay không.
- Không gọi snapshot là dữ liệu thời gian thực. Nó là ảnh chụp tại một mốc
  (`snapshot_retrieved_at`), và phiên cuối có thể chưa đóng cửa — xem giới hạn 4.
- Không trình bày PNJ đứng đầu như một gợi ý "bắt đáy". Đó là mô hình tuyến tính ngoại
  suy từ 38 dòng dữ liệu, đi kèm mức biến động cao nhất rổ — xem Bước 3.
- Không trình bày số trên dashboard như kết quả backtest. Đó là hai thứ khác nhau.
- Không nói mô hình "dự báo đúng" hay "có độ chính xác cao": AUC ngoài mẫu hiện chỉ
  khoảng 0,48–0,53 — xem giới hạn 5.

Ràng buộc đầy đủ về cách diễn giải nằm ở [`CONTRACTS_VI.md` §3.1](CONTRACTS_VI.md).

## 8. Tài liệu liên quan

- Hợp đồng data/feature/model/API: [`CONTRACTS_VI.md`](CONTRACTS_VI.md)
- Pipeline dữ liệu thị trường: [`DATA_PIPELINE.md`](DATA_PIPELINE.md)
- Phân công và quy trình: [`TEAM_BRIEFING_VI.md`](TEAM_BRIEFING_VI.md),
  [`../CONTRIBUTING.md`](../CONTRIBUTING.md)
