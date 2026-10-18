from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from functools import lru_cache
import logging
import os
from pathlib import Path

import pandas as pd
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse

from .contracts import (
    DataMode,
    Forecast,
    ForecastResponse,
    RecommendationRequest,
    RecommendationResponse,
    RiskProfile,
)
from .data import MarketSnapshot, load_csv_snapshot
from .features import FEATURE_COLUMNS, build_features
from .model import BaselineModel
from .refresh import (
    VN_TIMEZONE,
    RefreshConfig,
    RefreshState,
    SnapshotRefresher,
    last_session_is_intraday,
    now_vn,
    refresh_target,
    to_vn,
)

logger = logging.getLogger(__name__)

app = FastAPI(title="Vietnam Stock Forecasting MVP", version="0.1.0")
# Shown verbatim on the Vietnamese dashboard, so it is user-facing text and must be in
# Vietnamese like every other message (docs/CONTRACTS_VI.md section 4.1).
DISCLAIMER = (
    "Đây là công cụ nghiên cứu hỗ trợ ra quyết định, không phải tư vấn đầu tư cá nhân. "
    "Dự báo luôn có sai số và không cam kết lợi nhuận."
)
HORIZON_DAYS = 20
DEFAULT_SNAPSHOT_NAME = "vn30_snapshot.csv"
DEFAULT_LIVE_SNAPSHOT_NAME = "vn30_live.csv"
DEFAULT_STALE_AFTER_DAYS = 7
# One year, matching the bundled snapshot (16/10/2025 -> 16/10/2026), so switching to
# the live file does not silently change how much history the model trains on.
DEFAULT_REFRESH_HISTORY_DAYS = 365
DEFAULT_REFRESH_RETRY_MINUTES = 30
MAX_LISTED_SYMBOLS = 10

# One refresher per process; see refresh.py for the locking rules.
REFRESHER = SnapshotRefresher()

ONLINE_DISABLED_DETAIL = "Chế độ làm mới trực tuyến chưa được bật trong MVP."
SNAPSHOT_UNAVAILABLE_DETAIL = (
    "Không đọc được snapshot dữ liệu. Kiểm tra đường dẫn snapshot và tài liệu chạy demo."
)
NOT_ENOUGH_HISTORY_DETAIL = (
    f"Snapshot không đủ lịch sử để tạo dự báo. Mỗi mã cần ít nhất {HORIZON_DAYS} phiên "
    f"để tính feature và thêm {HORIZON_DAYS} phiên để có nhãn huấn luyện."
)
CONFIRMATION_REQUIRED_DETAIL = (
    "Cần xác nhận trước khi tạo danh sách nghiên cứu. Hãy chọn mức rủi ro và đánh dấu "
    "xác nhận rằng bạn hiểu đây là công cụ nghiên cứu, không phải tư vấn đầu tư."
)

# TẠM THỜI — chờ lane Modeling (Issue #5) chốt, xem docs/CONTRACTS_VI.md muc 4.3 / Q4.
# Lane App sở hữu cơ chế xếp hạng; lane Modeling sở hữu các con số dưới đây. Trọng số
# của mỗi profile cộng lại bằng 1 nên điểm luôn nằm trong [0, 1] và so sánh được.
RISK_PROFILE_WEIGHTS: dict[RiskProfile, dict[str, float]] = {
    RiskProfile.conservative: {
        "probability": 0.5,
        "expected_return": 0.1,
        "low_volatility": 0.4,
        "top_n": 3,
    },
    RiskProfile.balanced: {
        "probability": 0.4,
        "expected_return": 0.3,
        "low_volatility": 0.3,
        "top_n": 5,
    },
    RiskProfile.growth: {
        "probability": 0.3,
        "expected_return": 0.6,
        "low_volatility": 0.1,
        "top_n": 5,
    },
}
RISK_PROFILE_LABELS = {
    RiskProfile.conservative: "thận trọng",
    RiskProfile.balanced: "cân bằng",
    RiskProfile.growth: "tăng trưởng",
}


def _repo_root() -> Path:
    """Repository root inferred from this file, so paths do not depend on the shell CWD."""
    return Path(__file__).resolve().parents[2]


def _resolve(value: str) -> Path:
    """Relative paths resolve against the repository root, not the current directory."""
    path = Path(value).expanduser()
    return path if path.is_absolute() else _repo_root() / path


def _data_dir() -> Path:
    return _resolve(os.environ.get("DATA_DIR", "").strip() or "data")


def _default_snapshot() -> Path:
    """Resolve the snapshot path from the environment, then fall back to the bundled sample.

    Priority: SNAPSHOT_PATH (full path) > DATA_DIR/sample/<default name> > repo data/sample.
    """
    explicit = os.environ.get("SNAPSHOT_PATH", "").strip()
    if explicit:
        return _resolve(explicit)
    return _data_dir() / "sample" / DEFAULT_SNAPSHOT_NAME


def _live_snapshot() -> Path:
    """Where the background refresh writes. Defaults under data/raw/, which git ignores,
    so a running app never dirties the repository or the dataset submitted with it."""
    explicit = os.environ.get("LIVE_SNAPSHOT_PATH", "").strip()
    if explicit:
        return _resolve(explicit)
    return _data_dir() / "raw" / DEFAULT_LIVE_SNAPSHOT_NAME


def _env_flag(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int, *, minimum: int = 0) -> int:
    raw = os.environ.get(name, "").strip()
    try:
        return max(minimum, int(raw)) if raw else default
    except ValueError:
        logger.warning("Invalid %s=%r, using %s", name, raw, default)
        return default


def _refresh_enabled() -> bool:
    """Off unless asked for: tests, CI and a frozen demo must never reach the network."""
    return _env_flag("ALLOW_ONLINE_REFRESH")


def _refresh_config() -> RefreshConfig:
    return RefreshConfig(
        live_path=_live_snapshot(),
        history_days=_env_int("REFRESH_HISTORY_DAYS", DEFAULT_REFRESH_HISTORY_DAYS, minimum=30),
        retry_after=timedelta(
            minutes=_env_int("REFRESH_RETRY_MINUTES", DEFAULT_REFRESH_RETRY_MINUTES, minimum=1)
        ),
    )


def _load_snapshot(path: Path) -> MarketSnapshot:
    """Load a snapshot, mapping data problems to 503 instead of a traceback."""
    try:
        return load_csv_snapshot(path)
    except FileNotFoundError:
        logger.warning("Snapshot not found: %s", path)
        raise HTTPException(status_code=503, detail=SNAPSHOT_UNAVAILABLE_DETAIL) from None
    except ValueError:
        logger.exception("Snapshot failed validation: %s", path)
        raise HTTPException(status_code=503, detail=SNAPSHOT_UNAVAILABLE_DETAIL) from None


def _build_feature_frame(snapshot: MarketSnapshot) -> pd.DataFrame:
    try:
        return build_features(snapshot.frame, horizon_days=HORIZON_DAYS)
    except ValueError:
        logger.exception("Feature construction failed for snapshot: %s", snapshot.source)
        raise HTTPException(status_code=503, detail=SNAPSHOT_UNAVAILABLE_DETAIL) from None


def _stale_after_days() -> int:
    """Staleness threshold in days; see docs/CONTRACTS_VI.md section 4.2 (Q2)."""
    return _env_int("SNAPSHOT_STALE_DAYS", DEFAULT_STALE_AFTER_DAYS)


def _parse_iso_date(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        logger.warning("Snapshot metadata holds an unparsable date: %r", value)
        return None


def _data_age_warning(data_end: date | None) -> str | None:
    """State the data age plainly instead of raising an alarm: an offline research
    snapshot ending in the past is a deliberate choice, not a malfunction."""
    if data_end is None:
        return None
    # Age is measured against the Vietnamese trading day, not UTC: between 00:00 and
    # 07:00 Vietnam time the UTC date is still the previous day, which would understate
    # the age by one day for exactly the users this tool is built for.
    age_days = (now_vn().date() - data_end).days
    if age_days < 0:
        # A session that has not happened yet cannot have OHLCV data. validate_market_frame
        # already rejects future rows in the CSV, but `end_date` comes from the metadata
        # sidecar as written, so a sidecar that disagrees with its CSV still reaches here.
        # Never stay silent about it.
        return (
            f"Snapshot chứa dữ liệu đến ngày {data_end.strftime('%d/%m/%Y')}, tức là trong "
            f"tương lai ({-age_days} ngày). Dữ liệu này không hợp lệ — kiểm tra lại nguồn "
            "snapshot hoặc đồng hồ hệ thống trước khi dùng kết quả."
        )
    if age_days <= _stale_after_days():
        return None
    return (
        f"Snapshot kết thúc ngày {data_end.strftime('%d/%m/%Y')}, cũ {age_days} ngày. "
        "Đây là dữ liệu nghiên cứu offline, không phải dữ liệu thời gian thực."
    )


def _intraday_warning(snapshot: MarketSnapshot, data_end: date | None) -> str | None:
    """Flag a last session captured before the market closed: its "close" is a mid-session
    price, yet nothing else in the data distinguishes it from a real closing price."""
    if not last_session_is_intraday(data_end, snapshot.retrieved_at):
        return None
    taken = to_vn(snapshot.retrieved_at)
    return (
        f"Phiên {data_end.strftime('%d/%m/%Y')} được lấy lúc {taken.strftime('%H:%M')} giờ "
        "Việt Nam, trước giờ đóng cửa (15:00), nên giá của phiên này có thể là giá giữa "
        "phiên, chưa phải giá chốt."
    )


def _refresh_notice(state: RefreshState, data_end: date | None, *, still_due: bool) -> str | None:
    """Tell the user what the background refresh is doing to the numbers on screen.

    A failure is reported only while the snapshot in use still needs that refresh: once a
    newer snapshot arrives by another route, an old failure says nothing about the data.
    """
    shown = data_end.strftime("%d/%m/%Y") if data_end else "không rõ"
    if state.running and state.started_at:
        return (
            "Đang cập nhật dữ liệu thị trường mới trong nền (bắt đầu lúc "
            f"{state.started_at.strftime('%H:%M %d/%m')}). Bảng hiện vẫn dùng snapshot đến "
            f"ngày {shown}; tải lại sau vài phút để xem dữ liệu mới."
        )
    if state.outcome == "error" and state.finished_at and still_due:
        return (
            "Lần cập nhật dữ liệu gần nhất (lúc "
            f"{state.finished_at.strftime('%H:%M %d/%m')}) không thành công. Bảng hiện dùng "
            f"snapshot đến ngày {shown}; hệ thống sẽ tự thử lại sau."
        )
    return None


def _degenerate_model_warning(train_frame: pd.DataFrame) -> str | None:
    """Warn when the training window holds a single outcome class.

    That is the exact condition `BaselineModel.fit` uses to fall back to
    `DummyClassifier(strategy="prior")`, after which every prediction is the base rate
    rather than a learned signal — on a monotone snapshot that means a flat 100%.

    Checked on the labels, not inferred from the predictions: "all probabilities are
    equal" is only a proxy, and it is both wrong for a one-symbol snapshot (one value is
    trivially uniform) and silent in the case that matters most.
    """
    labels = train_frame["target_positive"].dropna().unique()
    if len(labels) != 1:
        return None
    direction = "tăng" if int(labels[0]) == 1 else "giảm"
    return (
        f"Dữ liệu huấn luyện chỉ có một lớp kết quả: mọi phiên đều {direction} sau "
        f"{HORIZON_DAYS} phiên. Mô hình không học được gì từ đó và chỉ trả về tỷ lệ nền, "
        "nên các con số dưới đây không mang thông tin dự báo. Không dùng để ra quyết định."
    )


def _vi_pct(value: float, digits: int = 1, *, signed: bool = False) -> str:
    """Vietnamese decimal comma, matching the Intl formatting used by the dashboard."""
    text = f"{value:+.{digits}%}" if signed else f"{value:.{digits}%}"
    return text.replace(".", ",")


def _evidence(row) -> list[str]:
    """Per-symbol evidence built from that symbol's own feature values."""
    return [
        f"Động lượng 20 phiên: {_vi_pct(row.return_20d, signed=True)}",
        f"Động lượng 5 phiên: {_vi_pct(row.return_5d, signed=True)}",
        f"Biến động 20 phiên: {_vi_pct(row.volatility_20d)}",
        f"Khối lượng so với trung bình 20 phiên: {f'{row.volume_ratio_20d:.2f}'.replace('.', ',')}x",
        f"Khoảng cách so với MA20: {_vi_pct(row.distance_from_ma20, signed=True)}",
    ]


@app.get("/health")
def health() -> dict[str, object]:
    # `snapshot_refresh` is additive: clients reading only `status` keep working.
    return {
        "status": "ok",
        "service": "vietnam-stock-forecasting",
        "snapshot_refresh": REFRESHER.status(enabled=_refresh_enabled(), config=_refresh_config()),
    }


@app.get("/", response_class=HTMLResponse)
def home() -> str:
    return """<!doctype html>
<html lang="vi">
  <head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
    <title>Vietnam Stock Forecasting</title>
    <style>body{font-family:system-ui;max-width:1040px;margin:2rem auto;padding:0 1rem;line-height:1.5}
    table{border-collapse:collapse;width:100%}td,th{border:1px solid #ddd;padding:.5rem;text-align:left}
    th{background:#f6f6f6}td.num,th.num{text-align:right;font-variant-numeric:tabular-nums}
    .muted{color:#666}.meta{color:#444;font-size:.9rem;margin:.5rem 0}
    .note{color:#666;font-size:.8rem;font-weight:400}
    .warn{border-left:4px solid #b8860b;background:#fffbe6;padding:.6rem .8rem;margin:.5rem 0}
    .err{border-left:4px solid #b00020;background:#fdecef;padding:.6rem .8rem;margin:.5rem 0}
    details ul{margin:.4rem 0 0 1rem;padding:0}details summary{cursor:pointer}
    [hidden]{display:none}</style>
  </head>
  <body><h1>Dự báo thị trường Việt Nam</h1>
    <p class="muted">Công cụ nghiên cứu giáo dục; không đảm bảo lợi nhuận hay thay thế tư vấn tài chính.</p>
    <button id="load">Tải dự báo snapshot</button>
    <p class="meta" id="status" role="status"></p>
    <div id="messages"></div>
    <table><caption class="muted">Bảng xếp hạng theo xác suất tăng giá trong 20 phiên tới</caption>
    <thead><tr>
      <th scope="col">Mã</th>
      <th scope="col" class="num">Xác suất tăng</th>
      <th scope="col" class="num">Lợi nhuận kỳ vọng<br><span class="note">20 phiên</span></th>
      <th scope="col" class="num">Độ biến động</th>
      <th scope="col" class="num">Độ tin cậy của hệ thống<br><span class="note">không phải xác suất sinh lời</span></th>
      <th scope="col">Bằng chứng</th>
    </tr></thead>
    <tbody id="rows"></tbody></table>
    <p class="muted" id="footer"></p>

    <h2>Danh sách nghiên cứu theo mức rủi ro</h2>
    <p class="muted">Chỉ chạy sau khi bạn chọn mức rủi ro và xác nhận. Kết quả xếp hạng
      các mã đang hiển thị ở bảng trên; không phải tư vấn đầu tư.</p>
    <p>
      <label for="profile">Mức rủi ro:</label>
      <select id="profile">
        <option value="conservative">Thận trọng</option>
        <option value="balanced" selected>Cân bằng</option>
        <option value="growth">Tăng trưởng</option>
      </select>
    </p>
    <p>
      <label><input type="checkbox" id="confirm">
        Tôi hiểu đây là công cụ nghiên cứu giáo dục, không phải tư vấn đầu tư, và không
        cam kết lợi nhuận.</label>
    </p>
    <p><button id="advise" disabled>Tạo danh sách nghiên cứu</button>
      <span class="note" id="advise-hint">Cần tải dự báo và đánh dấu xác nhận trước.</span></p>
    <div id="advice"></div>
    <script>
      const FALLBACK = {
        400: "Cần xác nhận trước khi tạo danh sách.",
        422: "Snapshot không đủ lịch sử để tạo dự báo.",
        501: "Chế độ làm mới trực tuyến chưa được bật trong MVP.",
        503: "Không đọc được snapshot dữ liệu. Kiểm tra đường dẫn snapshot và tài liệu chạy demo."
      };
      const esc = value => String(value).replace(/[&<>"']/g,
        c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
      const pct = (value, digits, signed) => new Intl.NumberFormat("vi-VN", {
        style: "percent", minimumFractionDigits: digits, maximumFractionDigits: digits,
        signDisplay: signed ? "exceptZero" : "auto"
      }).format(value);
      const stamp = new Intl.DateTimeFormat("vi-VN",
        {dateStyle: "short", timeStyle: "short", timeZone: "Asia/Ho_Chi_Minh"});
      const day = new Intl.DateTimeFormat("vi-VN", {dateStyle: "short", timeZone: "Asia/Ho_Chi_Minh"});
      // en-CA formats as YYYY-MM-DD, so both sides parse as UTC midnight and the
      // difference is a whole number of Vietnamese calendar days.
      const vnToday = () => new Intl.DateTimeFormat("en-CA", {timeZone: "Asia/Ho_Chi_Minh"}).format(new Date());
      const ageText = iso => {
        const days = Math.round((Date.parse(vnToday()) - Date.parse(iso)) / 86400000);
        if (days < 0) return "ngày trong tương lai";
        return days === 0 ? "dữ liệu hôm nay" : `dữ liệu cách đây ${days} ngày`;
      };
      const box = (kind, text) => `<div class="${kind}">${esc(text)}</div>`;
      let loaded = [];

      document.querySelector("#load").onclick = async (event) => {
        const button = event.currentTarget;
        const status = document.querySelector("#status");
        const messages = document.querySelector("#messages");
        const rows = document.querySelector("#rows");
        const footer = document.querySelector("#footer");
        button.disabled = true;
        button.setAttribute("aria-busy", "true");
        status.textContent = "Đang tải...";
        messages.innerHTML = "";
        try {
          const response = await fetch("/forecasts");
          const data = await response.json();
          if (!response.ok) {
            // Drop the previous symbols too, otherwise the shortlist button stays
            // enabled and would post stale symbols from an older snapshot.
            loaded = [];
            rows.innerHTML = "";
            footer.textContent = "";
            document.querySelector("#advice").innerHTML = "";
            status.textContent = "Không tải được dự báo.";
            const detail = typeof data.detail === "string" ? data.detail : null;
            messages.innerHTML = box("err", detail || FALLBACK[response.status] || "Có lỗi xảy ra.");
            return;
          }
          status.textContent = [
            `Tạo lúc: ${stamp.format(new Date(data.generated_at))}`,
            `Chế độ: ${data.mode}`,
            `Nguồn: ${data.source || "không rõ"}`,
            `Dữ liệu: ${data.data_start ? day.format(new Date(data.data_start)) : "?"}`
              + ` → ${data.data_end ? day.format(new Date(data.data_end)) : "?"}`
              + (data.data_end ? ` (${ageText(data.data_end)})` : "")
          ].join(" | ");
          messages.innerHTML = (data.warnings || []).map(text => box("warn", text)).join("");
          rows.innerHTML = data.forecasts.map(item => `<tr>
            <td>${esc(item.symbol)}</td>
            <td class="num">${pct(item.positive_return_probability, 1, false)}</td>
            <td class="num">${pct(item.expected_return, 2, true)}</td>
            <td class="num">${pct(item.expected_volatility, 2, false)}</td>
            <td class="num">${pct(item.confidence, 1, false)}</td>
            <td><details><summary>Xem</summary><ul>${
              (item.evidence || []).map(line => `<li>${esc(line)}</li>`).join("")
            }</ul></details></td></tr>`).join("");
          const first = data.forecasts[0];
          footer.textContent = first
            ? `Mô hình: ${first.model_version} | Dự báo tính từ phiên ${day.format(new Date(first.as_of))}`
              + ` | Kỳ hạn: ${first.horizon_days} phiên | ${data.disclaimer}`
            : data.disclaimer;
          loaded = data.forecasts.map(item => item.symbol);
        } catch (error) {
          loaded = [];
          status.textContent = "Không kết nối được tới máy chủ.";
          messages.innerHTML = box("err", String(error));
        } finally {
          button.disabled = false;
          button.removeAttribute("aria-busy");
          refreshAdviseState();
        }
      };

      const confirmBox = document.querySelector("#confirm");
      const adviseButton = document.querySelector("#advise");
      const adviseHint = document.querySelector("#advise-hint");

      function refreshAdviseState() {
        const ready = confirmBox.checked && loaded.length > 0;
        adviseButton.disabled = !ready;
        adviseHint.textContent = ready
          ? ""
          : (loaded.length === 0
              ? "Cần tải dự báo trước."
              : "Cần đánh dấu xác nhận trước.");
      }
      confirmBox.onchange = refreshAdviseState;
      refreshAdviseState();

      adviseButton.onclick = async (event) => {
        const advice = document.querySelector("#advice");
        event.currentTarget.disabled = true;
        advice.innerHTML = "<p class=\\"muted\\">Đang tạo danh sách...</p>";
        try {
          const response = await fetch("/recommendations", {
            method: "POST",
            headers: {"Content-Type": "application/json"},
            body: JSON.stringify({
              confirmed: confirmBox.checked,
              risk_profile: document.querySelector("#profile").value,
              symbols: loaded
            })
          });
          const data = await response.json();
          if (!response.ok) {
            const detail = typeof data.detail === "string" ? data.detail : null;
            advice.innerHTML = box("err", detail || FALLBACK[response.status] || "Có lỗi xảy ra.");
            return;
          }
          advice.innerHTML = (data.symbols.length
              ? `<ol>${data.symbols.map(symbol => `<li>${esc(symbol)}</li>`).join("")}</ol>`
              : "<p>Không có mã nào phù hợp.</p>")
            + `<p class="muted">${esc(data.message)}</p>`;
        } catch (error) {
          advice.innerHTML = box("err", String(error));
        } finally {
          refreshAdviseState();
        }
      };
    </script>
  </body>
</html>"""


@lru_cache(maxsize=4)
def _cached_forecasts(
    path_str: str, mtime_ns: int, size: int, sidecar_mtime_ns: int
) -> tuple[MarketSnapshot, tuple[Forecast, ...], tuple[str, ...]]:
    """Load, featurise, train and predict — cached per snapshot file revision.

    The cache key is the identity of the CSV (path, mtime, size) plus the mtime of its
    metadata sidecar, so replacing either on disk invalidates it without restarting the
    app. Those values are part of the key only for that reason; the body does not read them.

    Nothing time-dependent may be computed here: warnings are derived per request in
    `_snapshot_forecasts` so that a long-running server does not freeze the data age.
    HTTPException is not cached by lru_cache, so a bad snapshot is retried next request.
    """
    snapshot = _load_snapshot(Path(path_str))
    featured = _build_feature_frame(snapshot)

    # Training needs labels; inference does not. Keeping them in one frame would drop the
    # most recent `horizon_days` sessions — the rows we actually want to forecast from.
    train_frame = featured.dropna(subset=FEATURE_COLUMNS + ["forward_return", "target_positive"])
    predict_frame = (
        featured.dropna(subset=FEATURE_COLUMNS)
        .sort_values(["date", "symbol"])
        .groupby("symbol")
        .tail(1)
    )
    if train_frame.empty or predict_frame.empty:
        raise HTTPException(status_code=422, detail=NOT_ENOUGH_HISTORY_DETAIL)

    try:
        model = BaselineModel(estimator=None).fit(train_frame)
    except ValueError:
        logger.exception("Model training failed for snapshot: %s", snapshot.source)
        raise HTTPException(status_code=422, detail=NOT_ENOUGH_HISTORY_DETAIL) from None

    probabilities = model.predict_probability(predict_frame)
    expected_returns = model.predict_expected_return(predict_frame)
    items = [
        Forecast(
            symbol=row.symbol,
            as_of=row.date.date(),
            horizon_days=HORIZON_DAYS,
            positive_return_probability=float(probability),
            expected_return=float(expected_return),
            expected_volatility=float(row.volatility_20d),
            confidence=float(abs(probability - 0.5) * 2),
            evidence=_evidence(row),
            model_version=model.version,
            data_timestamp=snapshot.retrieved_at.to_pydatetime(),
        )
        for row, probability, expected_return in zip(
            predict_frame.itertuples(), probabilities, expected_returns, strict=True
        )
    ]
    # Ranked table: most likely to rise first, higher expected return breaking ties.
    items.sort(key=lambda item: (-item.positive_return_probability, -item.expected_return))
    # Degeneracy is a property of the data, not of the clock, so it is safe to cache.
    data_warnings = tuple(
        message for message in (_degenerate_model_warning(train_frame),) if message
    )
    return snapshot, tuple(items), data_warnings


CachedForecasts = tuple[MarketSnapshot, tuple[Forecast, ...], tuple[str, ...]]


def _forecasts_for(path: Path) -> CachedForecasts:
    try:
        stat = path.stat()
    except OSError:
        logger.warning("Snapshot not reachable: %s", path)
        raise HTTPException(status_code=503, detail=SNAPSHOT_UNAVAILABLE_DETAIL) from None
    # Same lookup order as load_csv_snapshot, so the key follows the sidecar it reads.
    sidecar_mtime_ns = 0
    for sidecar in (path.with_suffix(".metadata.json"), path.with_suffix(".json")):
        try:
            sidecar_mtime_ns = sidecar.stat().st_mtime_ns
            break
        except OSError:
            continue
    return _cached_forecasts(str(path), stat.st_mtime_ns, stat.st_size, sidecar_mtime_ns)


def _freshness(result: CachedForecasts) -> tuple[date, datetime]:
    snapshot = result[0]
    return (_parse_iso_date(snapshot.end_date) or date.min, to_vn(snapshot.retrieved_at))


def _active_forecasts() -> CachedForecasts:
    """Pick the snapshot to answer from: the live file when refresh is on and it is valid
    and fresher, otherwise the configured snapshot.

    With refresh off the live file is ignored even if it exists, so turning the flag off
    (for example on demo day) freezes the numbers on the configured snapshot.
    """
    base_path = _default_snapshot()
    live_path = _live_snapshot()
    if not _refresh_enabled() or live_path == base_path or not live_path.is_file():
        return _forecasts_for(base_path)
    try:
        live = _forecasts_for(live_path)
    except HTTPException:
        logger.warning("Live snapshot unusable, falling back to %s", base_path)
        return _forecasts_for(base_path)
    try:
        base = _forecasts_for(base_path)
    except HTTPException:
        return live
    # The configured snapshot wins ties, and also wins outright when someone has
    # committed a newer one than the last refresh produced.
    return live if _freshness(live) > _freshness(base) else base


def _snapshot_forecasts() -> tuple[MarketSnapshot, list[Forecast], list[str]]:
    """Return the ranked forecast list plus non-blocking warnings for the current snapshot.

    Shared by /forecasts and /recommendations so a shortlist can never be built from
    numbers that differ from the ones shown on the dashboard.
    """
    snapshot, items, data_warnings = _active_forecasts()
    if _refresh_enabled():
        # Never waits on the network: the fetch runs in a background thread and the
        # numbers below come from the snapshot already on disk.
        REFRESHER.maybe_start(snapshot, _refresh_config())

    data_end = _parse_iso_date(snapshot.end_date)
    notices = (
        _data_age_warning(data_end),
        _intraday_warning(snapshot, data_end),
        _refresh_notice(
            REFRESHER.state,
            data_end,
            still_due=refresh_target(snapshot.retrieved_at, now_vn()) is not None,
        ),
    )
    warnings = [text for text in notices if text] + list(data_warnings)
    return snapshot, list(items), warnings


@app.get("/forecasts", response_model=ForecastResponse)
def forecasts(mode: DataMode = DataMode.snapshot) -> ForecastResponse:
    if mode is DataMode.online:
        raise HTTPException(status_code=501, detail=ONLINE_DISABLED_DETAIL)
    snapshot, items, warnings = _snapshot_forecasts()
    return ForecastResponse(
        mode=mode,
        generated_at=datetime.now(timezone.utc),
        forecasts=items,
        disclaimer=DISCLAIMER,
        source=snapshot.source,
        data_start=_parse_iso_date(snapshot.start_date),
        data_end=_parse_iso_date(snapshot.end_date),
        snapshot_retrieved_at=snapshot.retrieved_at.to_pydatetime(),
        warnings=warnings,
    )


def _min_max(values: list[float]) -> list[float]:
    """Scale to [0, 1]; a flat series scores neutral so it cannot dominate the ranking."""
    low, high = min(values), max(values)
    if high == low:
        return [0.5] * len(values)
    span = high - low
    return [(value - low) / span for value in values]


def _rank_for_profile(candidates: list[Forecast], profile: RiskProfile) -> list[Forecast]:
    """Rank a candidate set for one risk profile and cap it at that profile's top_n.

    Scores are relative to the candidate set, not absolute: the same symbol can rank
    differently depending on what it is compared against. Weights live in
    RISK_PROFILE_WEIGHTS and belong to lane Modeling (Q4).
    """
    if not candidates:
        return []
    weights = RISK_PROFILE_WEIGHTS[profile]
    probabilities = _min_max([item.positive_return_probability for item in candidates])
    returns = _min_max([item.expected_return for item in candidates])
    volatilities = _min_max([item.expected_volatility for item in candidates])
    scored = [
        (
            weights["probability"] * probability
            + weights["expected_return"] * expected_return
            + weights["low_volatility"] * (1 - volatility),
            item,
        )
        for item, probability, expected_return, volatility in zip(
            candidates, probabilities, returns, volatilities, strict=True
        )
    ]
    # Symbol as the last key keeps the order stable when scores tie exactly.
    scored.sort(key=lambda pair: (-pair[0], pair[1].symbol))
    return [item for _, item in scored[: int(weights["top_n"])]]


@app.post("/recommendations", response_model=RecommendationResponse)
def recommendations(request: RecommendationRequest) -> RecommendationResponse:
    if not request.confirmed:
        raise HTTPException(status_code=400, detail=CONFIRMATION_REQUIRED_DETAIL)

    snapshot, items, warnings = _snapshot_forecasts()
    by_symbol = {item.symbol: item for item in items}

    requested: list[str] = []
    for symbol in request.symbols:
        normalized = symbol.strip().upper()
        if normalized and normalized not in requested:
            requested.append(normalized)

    candidates = [by_symbol[symbol] for symbol in requested if symbol in by_symbol]
    missing = [symbol for symbol in requested if symbol not in by_symbol]
    if not candidates:
        raise HTTPException(
            status_code=422,
            detail=(
                "Không có mã nào trong yêu cầu xuất hiện trong snapshot hiện tại: "
                f"{', '.join(missing[:MAX_LISTED_SYMBOLS]) or 'danh sách rỗng'}."
            ),
        )

    shortlist = _rank_for_profile(candidates, request.risk_profile)
    data_end = _parse_iso_date(snapshot.end_date)

    # The contract fixes this response to four fields, so provenance, skipped symbols and
    # model warnings all have to travel inside `message`. See CONTRACTS_VI.md 4.3 / Q5.
    sentences = [
        f"Danh sách nghiên cứu theo mức rủi ro {RISK_PROFILE_LABELS[request.risk_profile]}: "
        f"{len(shortlist)}/{len(candidates)} mã được chọn, xếp theo điểm phù hợp với mức "
        "rủi ro đã chọn."
    ]
    if missing:
        # Cap the listing: the request size is not bounded by the contract, and dumping
        # hundreds of symbols would make the message unreadable.
        shown = ", ".join(missing[:MAX_LISTED_SYMBOLS])
        overflow = (
            f" và {len(missing) - MAX_LISTED_SYMBOLS} mã khác"
            if len(missing) > MAX_LISTED_SYMBOLS
            else ""
        )
        sentences.append(f"Bỏ qua {len(missing)} mã không có trong snapshot: {shown}{overflow}.")
    # Scores are relative to the candidate set and have no absolute floor, so a symbol
    # the model considers more likely to fall can still top a shortlist. Say so rather
    # than let the ranking imply the opposite.
    below_even = [item.symbol for item in shortlist if item.positive_return_probability < 0.5]
    if below_even:
        sentences.append(
            f"Lưu ý: {len(below_even)}/{len(shortlist)} mã trong danh sách có xác suất tăng "
            f"dưới 50% ({', '.join(below_even)}). Xếp hạng là tương đối trong nhóm được so "
            "sánh, không có ngưỡng tối thiểu."
        )
    sentences.append(
        f"Dựa trên snapshot đến ngày {data_end.strftime('%d/%m/%Y') if data_end else 'không rõ'}"
        f" (nguồn {snapshot.source}), kỳ hạn {HORIZON_DAYS} phiên."
    )
    sentences.extend(warnings)
    sentences.append(
        "Đây là kết quả nghiên cứu, không phải tư vấn đầu tư và không cam kết lợi nhuận. "
        "Hãy đọc bằng chứng và chỉ số rủi ro của từng mã trước khi quyết định."
    )

    return RecommendationResponse(
        confirmed=True,
        risk_profile=request.risk_profile,
        symbols=[item.symbol for item in shortlist],
        message=" ".join(sentences),
    )
