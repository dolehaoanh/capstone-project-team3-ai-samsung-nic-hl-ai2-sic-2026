# Hợp đồng tích hợp MVP

Tài liệu này là nguồn sự thật chung cho 5 lane. Không tự ý đổi tên cột, kiểu dữ liệu,
ý nghĩa target hoặc JSON response. Nếu cần thay đổi, mở PR cập nhật tài liệu, code và
test cùng lúc.

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

Metadata đi kèm snapshot nên lưu dưới dạng JSON cùng tên file, ví dụ
`vn30_2024-01-01_2026-10-15.metadata.json`:

```json
{
  "source": "vnstock:VCI",
  "retrieved_at": "2026-10-15T08:00:00Z",
  "start": "2024-01-01",
  "end": "2026-10-15",
  "symbols": ["AAA", "BBB"],
  "frequency": "1d",
  "adjusted": false
}
```

Không commit dữ liệu cá nhân, API key hoặc dữ liệu không rõ giấy phép.

## 2. Feature và target

### MVP đã chốt

- Horizon: **20 phiên giao dịch** (xấp xỉ một tháng), lưu trong `horizon_days=20`.
- `return_5d`: `close[t] / close[t-5] - 1`.
- `return_20d`: `close[t] / close[t-20] - 1`.
- `volatility_20d`: độ lệch chuẩn rolling 20 của daily return.
- `volume_ratio_20d`: volume hiện tại chia cho rolling-mean volume 20 phiên.
- `distance_from_ma20`: `close[t] / MA20[t] - 1`.
- `forward_return`: `close[t+20] / close[t] - 1`.
- `target_positive`: `1` nếu `forward_return > 0`, ngược lại `0`.

Feature tại thời điểm `t` chỉ được dùng dữ liệu có timestamp **không muộn hơn `t`**.
Forward target được dùng để train/evaluate, không được đưa vào input feature.
Các dòng thiếu lịch sử hoặc chưa có `t+20` phải bị loại khỏi train/evaluation.

Các feature mới như RSI, MACD, VN-Index hoặc sector benchmark được phép bổ sung,
nhưng phải cập nhật feature dictionary và leakage test; không đổi ý nghĩa feature MVP.

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

Không diễn giải `confidence` là “xác suất đúng”. Không được hứa hẹn lợi nhuận.

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

`mode` nhận `snapshot` hoặc `online`. MVP bắt buộc hỗ trợ `snapshot`; `online` có thể
trả `501` nếu refresh chưa được bật.

Response `200`:

```json
{
  "mode": "snapshot",
  "generated_at": "2026-10-15T08:00:00Z",
  "forecasts": [],
  "disclaimer": "..."
}
```

Mỗi phần tử trong `forecasts` tuân theo Forecast contract ở trên.

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

## 5. Quy tắc tích hợp

1. Data lane có thể dùng fixture CSV; không cần chờ API online.
2. Feature lane có thể dùng fixture theo Data schema.
3. Modeling lane có thể dùng fixture/features hiện tại; không chờ dữ liệu thật.
4. App lane dùng mock JSON theo API contract; không chờ model hoàn chỉnh.
5. Mọi thay đổi contract phải có PR riêng hoặc được nêu rõ trong PR của module.
6. Thứ tự merge khuyến nghị: data schema → features → modeling → backend/UI.
