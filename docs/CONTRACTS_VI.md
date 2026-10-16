# Hợp đồng tích hợp MVP

Tài liệu này là nguồn sự thật chung cho 5 lane. Không tự ý đổi tên cột, kiểu dữ liệu,
ý nghĩa target hoặc JSON response. Nếu cần thay đổi, mở PR cập nhật tài liệu, code và
test cùng lúc.

> **Trạng thái bản này:** chứa đề xuất của lane App (Issue #1), chờ Owner gộp với
> đề xuất của các lane khác thành bản final. Các mục đánh dấu **[CHỜ CHỐT]** là đề
> xuất mặc định, chưa được phê duyệt; tổng hợp ở [§6](#6-câu-hỏi-cần-owner-chốt).
> Sau khi Owner quyết, xóa §6 và bỏ các dấu [CHỜ CHỐT].

## 1. Data schema — OHLCV

File snapshot CSV bắt buộc có đúng các cột logic sau:

| Cột | Kiểu | Ý nghĩa | Quy tắc |
|---|---|---|---|
| `date` | ISO date `YYYY-MM-DD` | Ngày giao dịch | Không rỗng; một dòng cho mỗi `symbol`/`date` |
| `symbol` | string | Mã cổ phiếu | Viết hoa, không khoảng trắng đầu/cuối |
| `open` | float >= 0 | Giá mở cửa | Đơn vị theo nguồn dữ liệu |
| `high` | float >= 0 | Giá cao nhất | `high >= low` |
| `low` | float >= 0 | Giá thấp nhất | `low <= high` |
| `close` | float >= 0 | Giá đóng cửa | Dùng làm giá chính để tạo return |
| `volume` | float >= 0 | Khối lượng | Không âm |

Snapshot phải được sắp xếp theo `symbol`, sau đó `date`. Không ghi index vào CSV.

**Đơn vị giá:** theo nguồn dữ liệu. Với `vnstock:VCI`, giá là **nghìn VND** — trong
`vn30_snapshot_example.csv`, `close` của FPT nằm trong khoảng `62.15`–`103.43`, tức
62.150–103.430 đồng. Mọi chỗ hiển thị giá tuyệt đối trên UI hoặc báo cáo phải ghi rõ
đơn vị; các cột tỷ lệ phần trăm không bị ảnh hưởng.

**Chuẩn hóa `symbol`:** quy tắc viết hoa thuộc trách nhiệm lane Data khi ingest.
`validate_market_frame` hiện chỉ chặn giá trị rỗng/NaN và chỉ uppercase tạm thời để
kiểm tra trùng `(symbol, date)` — nên `"fpt"` vẫn lọt vào snapshot. Lane tiêu thụ
dữ liệu được phép giả định `symbol` đã viết hoa.

### 1.1. Metadata sidecar

Khi lưu snapshot bằng `write_csv_snapshot(...)`, hệ thống tự tạo file sidecar
`<tên_file>.metadata.json`. `load_csv_snapshot(...)` tự đọc lại để khôi phục
provenance vào `MarketSnapshot`.

```json
{
  "source": "vnstock:VCI",
  "retrieved_at": "2026-10-15T06:50:12.791246+00:00",
  "start_date": "2024-01-02",
  "end_date": "2024-06-28",
  "symbols": ["ACB", "BID", "CTG"],
  "row_count": 1200,
  "frequency": "1d",
  "adjusted": false
}
```

Sáu trường đầu là shape **đang được code sinh ra** (`MarketSnapshot.metadata_dict()`).
Bản tài liệu trước ghi `start`/`end` — đã sửa theo code.

`row_count` là **số dòng dữ liệu, không tính dòng header**. Hiện
`vn30_sample.metadata.json` ghi `52` trong khi file có 51 dòng dữ liệu; cần lane Data
sửa lại, vì metadata là provenance của bài nộp.

`frequency` và `adjusted` **chưa được code ghi**, cần lane Data bổ sung **[CHỜ CHỐT]**.
`adjusted` là trường quan trọng về nghiệp vụ, không phải trang trí: giá chưa điều chỉnh
chia tách/cổ tức sẽ tạo `forward_return` sai lệch lớn (một mã chia tách 1:2 cho ra
−50% giả), làm nhiễu cả model và backtest.

Giá trị trong `vn30_snapshot_example.csv` thấp hơn đáng kể giá niêm yết danh nghĩa cùng
kỳ (ví dụ ACB `14.78`–`17.93` trong nửa đầu 2024), nên chuỗi này **có dấu hiệu đã được
điều chỉnh**. Lane Data cần xác nhận và ghi đúng `adjusted`, thay vì để lane khác suy
đoán từ độ lớn của giá.

Không commit dữ liệu cá nhân, API key hoặc dữ liệu không rõ giấy phép.

### 1.2. Snapshot of record cho demo

**[CHỜ CHỐT]** — xem Q1 ở §6.

`.gitignore` đang chặn `data/raw/*` và `data/processed/*`, trong khi CLI lane Data mặc
định ghi ra `data/raw/vn30_snapshot.csv`. Hệ quả: **snapshot VN30 thật không bao giờ
được commit**, chỉ `data/sample/` có dữ liệu.

| File hiện có | Số mã | Số dòng | Khoảng dữ liệu | `source` |
|---|---|---|---|---|
| `data/sample/vn30_sample.csv` | 1 (`AAA`, dữ liệu tổng hợp) | 51 | 2024-01-01 → 2024-02-20 | `synthetic_vn30_sample` |
| `data/sample/vn30_snapshot_example.csv` | 10/30 | 1.200 | 2024-01-02 → 2024-06-28 | `vnstock:VCI` |

Đề xuất: lane Data commit **một** snapshot VN30 đủ 30 mã vào `data/sample/` kèm sidecar
metadata, và file đó là snapshot of record cho demo lẫn test. Đặt tên không chứa
`sample`/`example`, vì đây là dataset đi vào data card của bài nộp.

Không thể dùng `vn30_sample.csv` làm snapshot of record: file chỉ có một mã với giá
tăng gần như đơn điệu, nên `target_positive` chỉ còn một lớp, `BaselineModel` rơi vào
nhánh `DummyClassifier(strategy="prior")`, và dashboard hiển thị xác suất 100% cho mọi
mã. Kết quả đó vô nghĩa về mặt nghiên cứu và trái quy tắc diễn giải ở
[§3.1](#31-quy-tắc-hiển-thị-và-diễn-giải).

`vn30_snapshot_example.csv` dùng tạm được (10 mã, giá thật, có đủ hai lớp kết quả) nên
lane App không bị chặn trong lúc chờ Q1, nhưng nó chỉ có 120 phiên/mã. Sau khi trừ 20
phiên warmup rolling và 20 phiên cuối không có `forward_return`, mỗi mã chỉ còn khoảng
80 dòng dùng được — đủ để chạy demo, không đủ để backtest có ý nghĩa thống kê. Vì vậy
bản 30 mã nên có **ít nhất 3 năm** lịch sử và bao gồm cả giai đoạn thị trường giảm, để
`target_positive` không bị lệch về một lớp.

## 2. Feature và target

### MVP đã chốt

- Horizon: **20 phiên giao dịch** (xấp xỉ một tháng), lưu trong `horizon_days=20`.
- `return_5d`: `close[t] / close[t-5] - 1`.
- `return_20d`: `close[t] / close[t-20] - 1`.
- `volatility_20d`: độ lệch chuẩn rolling 20 của daily return.
- `volume_ratio_20d`: volume hiện tại chia cho rolling-mean volume 20 phiên.
- `distance_from_ma20`: `close[t] / MA20[t] - 1`.
- `forward_return`: `close[t+20] / close[t] - 1`; **`NaN`** khi chưa có phiên `t+20`.
- `target_positive`: `1` nếu `forward_return > 0`, `0` nếu `forward_return <= 0`, và
  **`pd.NA` khi `forward_return` là `NaN`**. Nhãn có ba trạng thái, không phải hai:
  "chưa biết" khác "sẽ giảm".

Feature tại thời điểm `t` chỉ được dùng dữ liệu có timestamp **không muộn hơn `t`**.
Forward target được dùng để train/evaluate, không được đưa vào input feature.
Các dòng thiếu lịch sử hoặc chưa có `t+20` phải bị loại khỏi train/evaluation.

**Mọi phép rolling/shift/pct_change phải được tính theo từng `symbol`.** Cửa sổ không
được tràn qua ranh giới mã. Mỗi feature rolling phải có test với fixture **ít nhất 2
symbol**, khẳng định giá của mã này không ảnh hưởng feature của mã khác.

Quy tắc này được thêm sau khi rà soát `volatility_20d`: phép `.rolling(20)` được áp lên
series đã nối của mọi mã, nên với mã thứ hai trở đi, khoảng 19 dòng đầu được tính bằng
dữ liệu của mã liền trước. Điểm nguy hiểm là các giá trị đó **không phải `NaN`**, nên
chúng không bị `dropna` loại và lặng lẽ vào tập huấn luyện. Toàn bộ fixture và snapshot
mặc định hiện tại chỉ có một mã nên không test nào phát hiện được. Theo dõi ở Issue #3.

Các feature mới như RSI, MACD, VN-Index hoặc sector benchmark được phép bổ sung,
nhưng phải cập nhật feature dictionary và leakage test; không đổi ý nghĩa feature MVP.

### 2.1. `target_positive` phải phân biệt "chưa biết" với "sẽ giảm"

Hiện `features.py` tính nhãn bằng `(frame["forward_return"] > 0).astype("Int64")`. Trong
pandas, `NaN > 0` cho `False`, nên **`target_positive` không bao giờ nhận giá trị NA**:
20 phiên cuối của mỗi mã — những phiên chưa có `t+20` nên chưa biết kết quả — bị gán
nhãn `0`, tức là "sẽ giảm".

Kiểm chứng trên một mã, `horizon_days=5`: `forward_return` có 5 giá trị `NaN`, nhưng
`target_positive` có **0** giá trị NA và 5 dòng đó đều mang nhãn `0`.

Hệ quả: `dropna(subset=[..., "target_positive"])` **không loại được** các dòng chưa có
tương lai, trái với yêu cầu ở §2 là "các dòng chưa có `t+20` phải bị loại khỏi
train/evaluation". Hiện chưa có hậu quả thực tế, nhưng chỉ vì `BaselineModel.fit` và
`walk_forward_evaluate` tình cờ `dropna` thêm trên `forward_return`. Tính đúng đắn đang
phụ thuộc vào việc mọi người gọi nhớ thêm một cột vào `subset` — đúng loại lỗi sẽ xuất
hiện khi có thành viên mới hoặc một hàm mới.

Hai quy tắc:

1. **Lane Features (Issue #3) sửa** để `target_positive` là `pd.NA` ở những dòng
   `forward_return` là `NaN`, kèm test khẳng định số dòng NA của hai cột bằng nhau.
2. **Trong lúc chờ sửa, mọi lane lọc tập huấn luyện phải `dropna` trên cả
   `forward_return` *và* `target_positive`**, không dựa vào một cột. Tập dùng để *dự
   báo* thì ngược lại: chỉ cần `FEATURE_COLUMNS` đầy đủ, không được yêu cầu có nhãn —
   nếu không, hệ thống sẽ bỏ qua đúng 20 phiên mới nhất, là những phiên người dùng quan
   tâm nhất.

## 3. Forecast contract

### Input của model

DataFrame có ít nhất các cột trong `FEATURE_COLUMNS` của
`src/vietnam_stock_forecasting/features.py`. Input không chứa `forward_return` hoặc
`target_positive`.

### Output của model

Mỗi forecast phải có:

| Trường | Kiểu | Ý nghĩa |
|---|---|---|
| `symbol` | string | Mã cổ phiếu |
| `as_of` | date | Ngày dữ liệu cuối dùng để dự báo |
| `horizon_days` | int | 20 trong MVP |
| `positive_return_probability` | float 0..1 | Xác suất forward return dương |
| `expected_return` | float | Lợi nhuận kỳ vọng, dạng thập phân; `0.05` = 5% |
| `expected_volatility` | float >= 0 | Độ biến động ước tính |
| `confidence` | float 0..1 | Độ tự tin của hệ thống, không phải xác suất lợi nhuận |
| `evidence` | list[string] | Các tín hiệu/nguồn giải thích |
| `model_version` | string | Phiên bản model |
| `data_timestamp` | datetime | Thời điểm snapshot được lấy |

### 3.1. Quy tắc hiển thị và diễn giải

Ràng buộc với mọi bề mặt hiển thị (dashboard, báo cáo, slide thuyết trình):

- Không diễn giải `confidence` là "xác suất đúng" hay "xác suất lãi". Nhãn trên UI phải
  là **"Độ tin cậy của hệ thống"**, kèm giải thích ngắn rằng đây không phải xác suất
  sinh lời.
- Không hứa hẹn lợi nhuận ở bất kỳ đâu, kể cả trong `message` dạng văn bản tự do.
- `expected_return` và `expected_volatility` là thập phân; UI nhân 100 và hiển thị `%`.
- Mỗi lần hiển thị forecast phải kèm **nguồn dữ liệu, ngày dữ liệu cuối và disclaimer**
  trong cùng một khung nhìn — không đặt disclaimer ở nơi người dùng phải cuộn mới thấy.
- `evidence` và `model_version` phải hiển thị được (có thể dạng mở rộng từng dòng), vì
  đây là phần giải thích được của sản phẩm.
- Khi sắp xếp hoặc lọc forecast, **không** được dùng `forward_return` hay
  `target_positive`: hai cột này là dữ liệu tương lai và vẫn còn trong DataFrame sau
  `build_features`.
- `confidence` hiện được tính trong `api.py` bằng `abs(p - 0.5) * 2`. Đây là công thức
  tạm cho MVP, thuộc quyền quyết định của lane Modeling; nếu chuyển sang `model.py`
  thì lane Modeling sửa, lane App chỉ đọc giá trị **[CHỜ CHỐT]**.

## 4. API contract

### `GET /health`

Response `200`:

```json
{
  "status": "ok",
  "service": "vietnam-stock-forecasting"
}
```

### `GET /forecasts?mode=snapshot`

`mode` nhận `snapshot` hoặc `online`. MVP bắt buộc hỗ trợ `snapshot`; `online` trả
`501` vì online refresh không được bật trong MVP **[CHỜ CHỐT]**.

Response `200`:

```json
{
  "mode": "snapshot",
  "generated_at": "2026-10-15T08:00:00Z",
  "forecasts": [],
  "disclaimer": "...",
  "source": "vnstock:VCI",
  "data_start": "2024-01-02",
  "data_end": "2024-06-28",
  "snapshot_retrieved_at": "2026-10-15T06:50:12Z",
  "warnings": []
}
```

Mỗi phần tử trong `forecasts` tuân theo Forecast contract ở trên, và **được sắp xếp
giảm dần theo `positive_return_probability`** (bảng xếp hạng).

Năm trường cuối là bổ sung của lane App **[CHỜ CHỐT]** (Q9 ở §6), tất cả **optional** để
không làm vỡ lane đang dùng contract cũ. Nguồn giá trị lấy từ `MarketSnapshot`, tức là
từ sidecar metadata ở [§1.1](#11-metadata-sidecar):

| Trường | Kiểu | Lấy từ | Ý nghĩa |
|---|---|---|---|
| `source` | string \| null | `snapshot.source` | Nguồn dữ liệu, ví dụ `vnstock:VCI` |
| `data_start` | date \| null | `snapshot.start_date` | Ngày dữ liệu đầu của snapshot |
| `data_end` | date \| null | `snapshot.end_date` | Ngày dữ liệu cuối của snapshot |
| `snapshot_retrieved_at` | datetime \| null | `snapshot.retrieved_at` | Thời điểm snapshot được lấy |
| `warnings` | list[string] | — | Cảnh báo không chặn, ví dụ tuổi dữ liệu |

Lý do cần: Briefing §4 yêu cầu dashboard hiển thị "timestamp, mode, **nguồn**", và PR
template có checkbox *"Source provenance and timestamps are preserved"*, nhưng
`ForecastResponse` hiện không có trường nào chứa nguồn. Dashboard đang gán `mode` vào
nhãn "Nguồn", tức là **hiển thị sai**: `mode` chỉ cho biết `snapshot`/`online`.

Chọn cách thêm trường optional thay vì tạo endpoint `/meta` riêng, để UI chỉ cần một
request và provenance không bị lệch khỏi tập forecast mà nó mô tả.

### 4.1. Mã lỗi và thông báo

Thông báo lỗi trả cho người dùng là **tiếng Việt** (giao diện tiếng Việt thì
`detail` tiếng Anh là lỗi sản phẩm).

| Tình huống | Mã | `detail` |
|---|---|---|
| Bình thường | `200` | — |
| `mode=online` | `501` | `Chế độ làm mới trực tuyến chưa được bật trong MVP.` |
| Snapshot không đủ lịch sử để tạo dự báo | `422` | `Snapshot không đủ lịch sử để tạo dự báo.` |
| Snapshot thiếu, sai định dạng hoặc không hợp lệ | `503` | `Không đọc được snapshot dữ liệu. Kiểm tra đường dẫn snapshot và tài liệu chạy demo.` |

`503` là mã bổ sung cho tình huống hiện chưa được xử lý: `load_csv_snapshot` raise
`FileNotFoundError` (file thiếu) hoặc `ValueError` (CSV không hợp lệ) mà không ai bắt,
nên API trả `500` kèm traceback. Đây là sửa lỗi, không phải lựa chọn thiết kế cần
Owner quyết.

Lưu ý khi làm UI: không phải mã lỗi nào cũng do code của ta sinh ra. Nếu client gửi
`risk_profile` sai enum, FastAPI tự trả `422` với `detail` là **list** chứ không phải
string, và nội dung bằng tiếng Anh ngoài tầm kiểm soát. Vì vậy UI phải chọn thông báo
**theo HTTP status**, chỉ dùng `detail` khi nó là string.

### 4.2. Quy tắc tuổi dữ liệu

**[CHỜ CHỐT]** — xem Q2 ở §6.

Briefing §4 yêu cầu có trạng thái "snapshot cũ" nhưng chưa có định nghĩa. Đề xuất:

- **Tuổi dữ liệu đo theo `data_end`**, không theo `snapshot_retrieved_at`. Lý do:
  `retrieved_at` chỉ cho biết lúc nào ta tải file, không cho biết dữ liệu mới tới đâu.
  `vn30_snapshot_example.metadata.json` có `retrieved_at` là 2026-10-15 nhưng dữ liệu
  dừng ở 2024-06-28 — chênh nhau khoảng 28 tháng.
- **Luôn hiển thị `data_end` và tuổi dữ liệu** trên dashboard, ở mọi trường hợp, với
  cách diễn đạt trung tính.
- Khi tuổi dữ liệu vượt **7 ngày**: vẫn trả `200`, thêm một chuỗi vào `warnings`, UI
  hiện dải thông báo. Nội dung phải **giải thích bản chất**, không chỉ báo động, ví dụ:
  *"Snapshot kết thúc ngày 28/06/2024, cũ N ngày. Đây là dữ liệu nghiên cứu offline,
  không phải dữ liệu thời gian thực."*
- Ngưỡng đọc từ biến môi trường `SNAPSHOT_STALE_DAYS` (mặc định `7`), để Owner đổi
  quyết định mà không cần sửa code.

- Nếu `data_end` nằm trong **tương lai**, đó không phải dữ liệu cũ mà là dữ liệu **không
  hợp lệ** (phiên chưa xảy ra thì không thể có OHLCV): phải cảnh báo riêng, không được
  im lặng. Đúng ra `validate_market_frame` nên chặn ngày tương lai ngay từ tầng dữ liệu
  — đề nghị lane Data bổ sung (Issue #4).

`warnings` còn chứa một cảnh báo thứ hai, độc lập với tuổi dữ liệu: khi tập huấn luyện
chỉ có **một lớp kết quả**, `BaselineModel` rơi vào `DummyClassifier(strategy="prior")`
và mọi dự báo chỉ còn là tỷ lệ nền — với snapshot đi một chiều thì là 100%. UI phải nói
rõ điều đó thay vì trưng con số đó ra như một dự báo tự tin.

Điều kiện phát hiện là **số lớp trong `target_positive` của tập huấn luyện**, không phải
"mọi xác suất bằng nhau". Suy từ xác suất là sai theo cả hai chiều: snapshot một mã
đương nhiên cho một giá trị (dương tính giả), còn nếu vì thế mà bỏ qua trường hợp một
mã thì lại im lặng đúng lúc cần cảnh báo nhất (âm tính giả).

**Không dùng cơ chế miễn trừ theo `source`.** Phương án miễn trừ cho dữ liệu mẫu từng
được xem xét nhưng bị loại vì hai lý do. Thứ nhất nó không chạy được trên dữ liệu thật:
`vn30_snapshot_example.csv` có `source` là `vnstock:VCI`, chỉ *tên file* chứa `example`,
nên mọi quy tắc khớp chuỗi trên `source` đều bỏ sót nó. Thứ hai, snapshot of record
đề xuất ở [§1.2](#12-snapshot-of-record-cho-demo) cũng nằm trong `data/sample/`, nên
miễn trừ theo đường dẫn sẽ vô hiệu hóa cảnh báo đúng lúc nó cần hoạt động.

Vấn đề thật không phải là có nên cảnh báo, mà là **cách diễn đạt**: một snapshot nghiên
cứu kết thúc năm 2024 là lựa chọn có chủ ý, không phải sự cố. Câu thông báo giải thích
được điều đó thì hiển thị suốt buổi demo vẫn hợp lý, và giữ được tính trung thực mà
[§3.1](#31-quy-tắc-hiển-thị-và-diễn-giải) yêu cầu.

### `POST /recommendations`

Request:

```json
{
  "confirmed": true,
  "risk_profile": "balanced",
  "symbols": ["AAA"]
}
```

`risk_profile` nhận `conservative`, `balanced` hoặc `growth`.
Nếu `confirmed=false`, API phải trả `400`; không tạo shortlist khi chưa có xác nhận.

Response `200`:

```json
{
  "confirmed": true,
  "risk_profile": "balanced",
  "symbols": ["AAA"],
  "message": "..."
}
```

### 4.3. Ngữ nghĩa shortlist

Schema trên **không đổi** trong MVP. Hệ quả cần cả nhóm thống nhất cách hiểu:

- `symbols` trong request là **bắt buộc** (`min_length=1`), nên server **chỉ lọc và xếp
  hạng lại danh sách client gửi**, không tự xếp hạng toàn bộ rổ VN30.
- `symbols` trong response là **tập con đã xếp hạng** của `symbols` request.
- `symbols` được so khớp sau khi `strip().upper()`, loại trùng và giữ thứ tự người dùng
  gửi; mã không có trong snapshot bị bỏ qua. Nếu **không mã nào** hợp lệ thì trả `422`
  kèm danh sách mã bị bỏ, không trả danh sách rỗng một cách im lặng.
- Response không có trường điểm số hay lý do từng mã, nên **`message` phải tải toàn bộ
  phần còn lại**: số mã được chọn trên tổng số ứng viên, mã bị bỏ qua, provenance
  (ngày dữ liệu cuối + `source` + kỳ hạn), các cảnh báo giống `warnings` của
  `/forecasts`, và câu miễn trừ trách nhiệm. Nếu nhóm muốn shortlist có điểm/lý do từng
  mã thì phải mở PR đổi mục này **trước** PR của lane App **[CHỜ CHỐT]**.
- Shortlist phải được tính từ **cùng một tập forecast** mà `/forecasts` trả về, không
  tính lại bằng đường khác, để con số trên dashboard và trong danh sách không lệch nhau.
- Tiêu chí từng `risk_profile` là quyết định của lane Modeling (Issue #5), lane App chỉ
  thực thi và hiển thị **[CHỜ CHỐT]**. Hướng đề xuất:

Cơ chế đã triển khai (lane App): chuẩn hóa min-max ba chỉ số **trong phạm vi tập ứng
viên**, rồi tính điểm bằng tổng có trọng số; trọng số mỗi profile cộng lại bằng `1` nên
điểm luôn trong `[0, 1]`. Chuỗi phẳng được cho `0.5` để không chi phối thứ hạng. Điểm
mang tính **tương đối**: cùng một mã có thể xếp khác nhau tùy tập đem ra so sánh.

| Profile | `probability` | `expected_return` | `low_volatility` | `top_n` |
|---|---|---|---|---|
| `conservative` | 0,5 | 0,1 | 0,4 | 3 |
| `balanced` | 0,4 | 0,3 | 0,3 | 5 |
| `growth` | 0,3 | 0,6 | 0,1 | 5 |

Các con số này nằm trong `RISK_PROFILE_WEIGHTS` ở `api.py` và là **giá trị tạm**: lane
Modeling sửa số, lane App không sửa cơ chế. Hiện **chưa có ngưỡng cắt cứng** nào (kiểu
"loại mọi mã có `expected_volatility` vượt X") — nếu lane Modeling muốn, cần nêu rõ cách
xử lý khi ngưỡng lọc hết toàn bộ ứng viên.

**Không có ngưỡng tối thiểu nên shortlist có thể chứa mã mà mô hình cho là dễ giảm hơn
tăng.** Trên snapshot 10 mã hiện tại, 7/10 mã có `positive_return_probability` dưới 50%,
và danh sách "thận trọng" gồm HPG ở 49,6% — tức là xếp hạng tương đối đang chọn mã tốt
nhất trong một nhóm mà phần lớn đều kém. Vì xếp hạng không tự nói ra điều đó, `message`
**phải nêu rõ** số mã trong danh sách có xác suất dưới 50%. Nếu lane Modeling muốn đặt
sàn (ví dụ loại mọi mã dưới 50%), đó là một phần của Q4.

### 4.4. `GET /` — yêu cầu dashboard

Trang chủ trả HTML tiếng Việt. Các yêu cầu sau đến từ Briefing §4, đưa vào contract để
review được:

- Bảng xếp hạng: `symbol`, xác suất tăng, lợi nhuận kỳ vọng, độ biến động, độ tin cậy.
- Hiển thị `generated_at`, `mode`, `source`, `data_end` và disclaimer.
- Thời gian hiển thị theo giờ Việt Nam (`Asia/Ho_Chi_Minh`); `generated_at` trong API là UTC.
- Trạng thái lỗi tường minh cho `501`, `422`, `503` và thông báo tuổi dữ liệu.
- Recommendation chỉ gọi được sau khi người dùng **chọn risk profile và đánh dấu xác
  nhận**.
- Tuân thủ đầy đủ [§3.1](#31-quy-tắc-hiển-thị-và-diễn-giải).

**Ngoài phạm vi MVP [CHỜ CHỐT]**: dashboard không so sánh với VN-Index,
buy-and-hold hay lãi suất tiết kiệm. README có nêu "benchmark comparisons" và lấy
"thắng lạm phát và lãi suất tiết kiệm" làm tiêu chí thành công, nhưng Briefing không
giao phần này cho lane nào; so sánh buy-and-hold hiện thuộc báo cáo của lane Modeling
(Briefing §3). Nếu Owner muốn đưa lên dashboard thì cần chỉ định nguồn số lãi suất và
lane chịu trách nhiệm.

## 5. Quy tắc tích hợp

1. Data lane có thể dùng fixture CSV; không cần chờ API online.
2. Feature lane có thể dùng fixture theo Data schema.
3. Modeling lane có thể dùng fixture/features hiện tại; không chờ dữ liệu thật.
4. App lane dùng mock JSON theo API contract; không chờ model hoàn chỉnh.
5. Mọi thay đổi contract phải có PR riêng hoặc được nêu rõ trong PR của module.
6. Thứ tự merge khuyến nghị: data schema → features → modeling → backend/UI.
7. Mỗi lane chỉ sửa file thuộc lane mình. Lane merge sau không tự sửa file của lane
   trước, kể cả khi thấy lỗi — báo chủ lane đó hoặc Owner. Lý do: lane App merge cuối
   chuỗi nên mọi lần chạm file chung đều thành xung đột ở PR cuối.
8. Đường dẫn snapshot trong code phải độc lập với thư mục chạy (hiện `api.py` dùng path
   tương đối nên test chỉ pass khi `pytest` chạy từ gốc repo), và phải override được qua
   biến môi trường.
9. Snapshot được cache trong tiến trình theo **(đường dẫn, `mtime`, kích thước)** của
   file. Hệ quả vận hành: sau khi lane Data ghi snapshot mới vào đúng đường dẫn đang
   dùng, **không cần restart app** — request kế tiếp tự nạp lại. Mọi giá trị phụ thuộc
   thời gian (tuổi dữ liệu) phải được tính ngoài cache, nếu không app chạy dài ngày sẽ
   báo tuổi dữ liệu đóng băng.
10. `.env.example` hiện chưa được code nào đọc (`DATA_DIR`, `MODEL_PATH`,
   `ALLOW_ONLINE_REFRESH` đều không có loader và repo không có `python-dotenv`). Lane
   nào dùng biến môi trường thì đọc `os.environ` trực tiếp kèm default an toàn, và ghi
   rõ trong tài liệu chạy demo; không thêm dependency vào `pyproject.toml` nếu không
   thật cần, vì đó là file dùng chung.

## 6. Câu hỏi cần Owner chốt

Tổng hợp các mục **[CHỜ CHỐT]**.

| # | Câu hỏi | Đề xuất mặc định | Nếu không chốt |
|---|---|---|---|
| Q1 | Snapshot of record cho demo là file nào? `.gitignore` chặn `data/raw/` nên snapshot thật không vào repo ([§1.2](#12-snapshot-of-record-cho-demo)) | Giữ nguyên `.gitignore`; lane Data commit snapshot VN30 đủ 30 mã, ≥3 năm, vào `data/sample/` với tên không chứa `sample`/`example` | Bài nộp không có dữ liệu để chạy lại, hoặc demo chạy trên 1 mã tổng hợp và hiện 100% cho mọi mã |
| Q2 | Ngưỡng và cách đo tuổi dữ liệu ([§4.2](#42-quy-tắc-tuổi-dữ-liệu)) | Theo `data_end`, ngưỡng 7 ngày, luôn hiển thị tuổi, thông báo giải thích thay vì báo động | Không có trạng thái này, hoặc dải cảnh báo đọc như sự cố giữa buổi demo |
| Q3 | `mode=online` giữ `501` hay bật thật, và ai nối? | Giữ `501` + UI giải thích | Lane App và lane Data chờ nhau |
| Q4 | Ai định nghĩa tiêu chí risk profile ([§4.3](#43-ngữ-nghĩa-shortlist))? | Lane Modeling đưa ngưỡng, lane App render | Hai lane đưa ra hai định nghĩa khác nhau |
| Q5 | Có đổi contract `/recommendations` để trả điểm/lý do từng mã? | Không đổi trong MVP | Lane App làm xong rồi phải sửa lại |
| Q6 | So sánh benchmark và lãi suất tiết kiệm có trong MVP? README hứa, Briefing không giao lane nào | Không làm trong MVP; buy-and-hold để lane Modeling báo cáo | Rơi mặc định vào lane App ở phút cuối |
| Q7 | Bổ sung `frequency` và `adjusted` vào metadata sidecar ([§1.1](#11-metadata-sidecar))? | Lane Data bổ sung, mặc định `adjusted: false` | `forward_return` sai ở mã chia tách/trả cổ tức mà không ai biết |
| Q8 | `confidence` nằm ở `api.py` hay `model.py` ([§3.1](#31-quy-tắc-hiển-thị-và-diễn-giải))? | Giữ `api.py` cho MVP, ghi rõ là tạm | Hai lane cùng sửa `api.py` |
| Q9 | Thêm 5 trường provenance optional vào `ForecastResponse` ([§4](#get-forecastsmodesnapshot))? | Thêm, vì dashboard đang hiển thị sai nhãn "Nguồn" và PR template yêu cầu giữ provenance | Không có cách nào hiển thị nguồn dữ liệu đúng, hoặc phải thêm endpoint `/meta` riêng |
