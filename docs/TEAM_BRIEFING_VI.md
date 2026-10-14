# Briefing triển khai dự án

## Mục tiêu MVP

Xây dựng ứng dụng web chạy local, giao diện tiếng Việt, hỗ trợ nghiên cứu nhóm cổ phiếu VN30. Ứng dụng dùng dữ liệu snapshot để chạy offline, có thể refresh online khi người dùng chủ động bật. Kết quả phải hiển thị xác suất tăng, lợi nhuận kỳ vọng, rủi ro, độ tin cậy, thời điểm dữ liệu và nguồn dữ liệu.

Đây là công cụ hỗ trợ nghiên cứu, không cam kết lợi nhuận. Không triển khai giao dịch tự động.

## Cách cả nhóm làm việc song song

1. Mỗi người nhận đúng Issue của mình và tạo branch riêng.
2. Các lane dùng đúng hợp đồng dữ liệu/API hiện có; nếu cần đổi hợp đồng thì mở PR nhỏ trước.
3. Có thể dùng Copilot, ChatGPT, Claude hoặc công cụ khác tùy thích; không chia sẻ token/quota giữa các thành viên.
4. Mỗi PR phải có test và mô tả bằng chứng kiểm thử.
5. Hao Anh review, giải quyết xung đột tích hợp và merge; không cần tự viết toàn bộ phần chuyên môn của người khác.

## Phân công

### 1. Luu Huy Wvu — Dữ liệu thị trường

**Mục tiêu:** thay dữ liệu mẫu bằng pipeline dữ liệu miễn phí, có thể lưu snapshot để chạy offline.

**Việc cần làm:**

- Dùng `vnstock` hoặc nguồn miễn phí phù hợp để lấy lịch sử OHLCV.
- Chuẩn hóa đúng 7 cột: `date`, `symbol`, `open`, `high`, `low`, `close`, `volume`.
- Lưu raw snapshot và metadata: nguồn, thời điểm lấy, khoảng ngày, danh sách mã.
- Kiểm tra dữ liệu rỗng, trùng ngày/mã, giá âm, thiếu cột và ngày không hợp lệ.
- Viết test không cần internet bằng fixture nhỏ.

**Không làm trong Issue này:** news/RAG, UPCOM, API trả phí, tự động giao dịch.

**Bàn giao:** PR có code, fixture, test, hướng dẫn chạy và ví dụ snapshot.

### 2. Tran Nguyen Ha V — Feature và chống leakage

**Mục tiêu:** tạo feature kỹ thuật và target trung hạn mà không nhìn thấy dữ liệu tương lai.

**Việc cần làm:**

- Bổ sung RSI, MACD, rolling volatility, momentum, moving-average distance, volume ratio.
- Thêm feature benchmark VN-Index/sector nếu schema dữ liệu đã sẵn sàng.
- Xác định rõ horizon và target; giữ nguyên point-in-time semantics.
- Viết test chứng minh thay đổi giá tương lai không làm thay đổi feature tại thời điểm quá khứ.
- Ghi rõ các dòng bị loại vì chưa đủ lịch sử hoặc chưa có forward target.

**Bàn giao:** PR có feature code, leakage tests, tài liệu feature dictionary.

### 3. Chu Thi Thu Hien — Mô hình và đánh giá

**Mục tiêu:** so sánh baseline với mô hình tabular đáng tin cậy bằng walk-forward evaluation.

**Việc cần làm:**

- Giữ baseline hiện tại làm mốc.
- Thử CatBoost/LightGBM nếu tương thích môi trường; nếu không, dùng sklearn.
- Đánh giá accuracy/F1/AUC/Brier/calibration và return, drawdown, volatility, Sharpe-like metric.
- So sánh với buy-and-hold và chiến lược đơn giản.
- Không shuffle dữ liệu theo thời gian và không fit trên test window.
- Lưu model version và báo cáo kết quả có ngày dữ liệu.

**Bàn giao:** PR có evaluation report mẫu, test walk-forward và tiêu chí chọn model.

### 4. Vu Tung Lam — Backend và giao diện

**Mục tiêu:** biến forecast contract thành dashboard tiếng Việt dễ demo.

**Việc cần làm:**

- Giữ các endpoint `/health`, `/forecasts`, `/recommendations` tương thích.
- Hiển thị bảng xếp hạng mã, xác suất tăng, lợi nhuận kỳ vọng, biến động, confidence.
- Hiển thị timestamp, mode snapshot/online, nguồn và disclaimer.
- Recommendation chỉ chạy sau khi người dùng xác nhận và chọn risk profile.
- Thêm trạng thái lỗi rõ ràng khi snapshot cũ, thiếu dữ liệu hoặc online refresh chưa bật.

**Bàn giao:** PR có UI/API test và hướng dẫn chạy demo.

### 5. Hao Anh — Tích hợp và phê duyệt

**Mục tiêu:** giữ phạm vi, hợp đồng và chất lượng chung; không trở thành điểm nghẽn.

**Việc cần làm:**

- Tạo/duy trì GitHub Project, Issues, labels, milestones và dependency.
- Review mọi PR theo checklist: test, leakage, provenance, API compatibility, disclaimer.
- Merge theo thứ tự hợp đồng: data schema → feature → model → API/UI.
- Chạy demo sạch trước khi nộp.
- Tổng hợp model card, data card, limitations và kịch bản thuyết trình.
- Nếu có tranh chấp scope, ưu tiên VN30 + snapshot offline + backtest reproducible; hoãn UPCOM, RAG phức tạp và mobile.

**Không cần làm:** tự viết toàn bộ data/model/UI. Vai trò chính là integration, review và quyết định phạm vi.

## Checklist nhận PR

- [ ] PR liên kết đúng Issue.
- [ ] Có test mới hoặc giải thích vì sao không cần.
- [ ] `python -m pytest -q` pass.
- [ ] `git diff --check` pass.
- [ ] Không có leakage hoặc dữ liệu tương lai.
- [ ] Có provenance/timestamp nếu đụng dữ liệu.
- [ ] Không làm hỏng contract trong `contracts.py`.
- [ ] Cập nhật README/tài liệu nếu thay đổi cách chạy.

## Lệnh bắt đầu

```bash
git fetch origin
git checkout -b <ten-thanh-vien>/<ten-cong-viec>
python -m pip install -e ".[dev]"
python -m pytest -q
```
