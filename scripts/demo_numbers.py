"""Print every number that docs/DEMO_VI.md quotes, recomputed from the current snapshot.

Run it after anything that can move those numbers: a new snapshot from lane Data, a
feature change from lane Features, a model change from lane Modeling.

    .venv/Scripts/python.exe scripts/demo_numbers.py              # all numbers, ~1 min
    .venv/Scripts/python.exe scripts/demo_numbers.py --skip-eval  # without walk-forward

Each line ends with ✓ when that exact text already appears in DEMO_VI.md and ✗ when it
does not, so a changed number points straight at the paragraph to update. A ✗ is not
always an error (the doc words some numbers differently); read the line before editing.

The snapshot is the one the app would serve: SNAPSHOT_PATH, else DATA_DIR, else
data/sample/vn30_snapshot.csv. Online refresh is forced off, so nothing is fetched.
"""

from __future__ import annotations

import argparse
import os
import re
from pathlib import Path
import sys

import numpy as np
import pandas as pd

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))
os.environ["ALLOW_ONLINE_REFRESH"] = "false"  # refresh is on by default (#22); set before api reads it

from fastapi.testclient import TestClient  # noqa: E402

from vietnam_stock_forecasting import api  # noqa: E402
from vietnam_stock_forecasting.evaluation import walk_forward_evaluate  # noqa: E402
from vietnam_stock_forecasting.features import FEATURE_COLUMNS  # noqa: E402
from vietnam_stock_forecasting.model import BaselineModel  # noqa: E402
from vietnam_stock_forecasting.refresh import to_vn  # noqa: E402

DEMO_DOC = REPO / "docs" / "DEMO_VI.md"
# Banks in the VN30 basket, for the "banks average" volatility in DEMO_VI.md step 3.
VN30_BANKS = {"ACB", "BID", "CTG", "HDB", "LPB", "MBB", "SHB", "SSB", "STB", "TCB", "TPB", "VCB", "VIB", "VPB"}
BIG_DROP = -0.30  # "giảm hơn 30% trong 20 phiên"
PROFILES = {"conservative": "Thận trọng", "balanced": "Cân bằng", "growth": "Tăng trưởng"}


def vi_num(value: float, digits: int) -> str:
    """1234.5 -> '1.234,5', the way the doc writes numbers."""
    text = f"{value:,.{digits}f}"
    return text.replace(",", "\0").replace(".", ",").replace("\0", ".")


def vi_pct(value: float, digits: int = 1, *, signed: bool = False) -> str:
    text = vi_num(abs(value) * 100, digits) + "%"
    if value < 0:
        return "−" + text  # the doc uses the minus sign U+2212 in tables
    return ("+" + text) if signed and value > 0 else text


class Report:
    def __init__(self, doc: str) -> None:
        # Line breaks inside a paragraph must not hide a number.
        self.doc = re.sub(r"\s+", " ", doc)
        self.missing = 0

    def found(self, value: str) -> bool:
        """Accept the spellings the doc uses: '-' or '−', and '50%' for '50,0%'."""
        value = re.sub(r"\s+", " ", value)
        variants = {value, value.replace("−", "-"), value.replace("-", "−")}
        variants |= {re.sub(r",0+%", "%", variant) for variant in variants}
        return any(variant in self.doc for variant in variants)

    def section(self, title: str) -> None:
        print(f"\n## {title}")

    def line(self, label: str, *values: str) -> None:
        """Print a fact; each value is looked up in the doc."""
        marks = []
        for value in values:
            found = self.found(value)
            self.missing += not found
            marks.append(f"{value} {'✓' if found else '✗'}")
        print(f"- {label}: " + " | ".join(marks) if marks else f"- {label}")

    def note(self, text: str) -> None:
        print(f"  {text}")


def floor_runs(frame: pd.DataFrame) -> list[tuple[pd.Timestamp, pd.Timestamp, int, float]]:
    """Runs of sessions where open = high = low = close (a locked limit move)."""
    locked = (frame["open"] == frame["high"]) & (frame["high"] == frame["low"]) & (frame["low"] == frame["close"])
    runs, start = [], None
    rows = frame.reset_index(drop=True)
    for index, is_locked in enumerate(locked.tolist() + [False]):
        if is_locked and start is None:
            start = index
        elif not is_locked and start is not None:
            first, last = rows.iloc[start], rows.iloc[index - 1]
            before = rows.iloc[start - 1]["close"] if start > 0 else first["open"]
            runs.append((first["date"], last["date"], index - start, last["close"] / before - 1))
            start = None
    return runs


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--skip-eval", action="store_true", help="skip walk-forward evaluation (slow)")
    args = parser.parse_args(argv)

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    report = Report(DEMO_DOC.read_text(encoding="utf-8") if DEMO_DOC.exists() else "")
    client = TestClient(api.app)

    snapshot = api._active_forecasts()[0]  # same file selection as the app
    response = client.get("/forecasts")
    if response.status_code != 200:
        print(f"/forecasts returned {response.status_code}: {response.json()}")
        return 1
    data = response.json()
    forecasts = data["forecasts"]

    market = snapshot.frame
    per_symbol = market.groupby("symbol").size()
    featured = api._build_feature_frame(snapshot)
    train = featured.dropna(subset=FEATURE_COLUMNS + ["forward_return", "target_positive"])

    print(f"# Số liệu cho docs/DEMO_VI.md — {snapshot.source}")
    report.section("Bước 1 — Bảng xếp hạng")
    sessions = f"{per_symbol.min()} phiên/mã" if per_symbol.min() == per_symbol.max() else (
        f"{per_symbol.min()}–{per_symbol.max()} phiên/mã")
    start, end = pd.Timestamp(data["data_start"]), pd.Timestamp(data["data_end"])
    report.line("Snapshot", f"{len(forecasts)} mã", sessions, f"{start:%d/%m/%Y} → {end:%d/%m/%Y}")
    report.note(f"{vi_num(len(market), 0)} dòng OHLCV")
    for item in forecasts[:3] + forecasts[-1:]:
        report.line(
            f"{item['symbol']}",
            f"| {item['symbol']} | {vi_pct(item['positive_return_probability'])} "
            f"| {vi_pct(item['expected_return'], 2, signed=True)} "
            f"| {vi_pct(item['expected_volatility'], 2)} | {vi_pct(item['confidence'])} |",
        )
    probabilities = [item["positive_return_probability"] for item in forecasts]
    below = sum(p < 0.5 for p in probabilities)
    report.line("Dưới 50%", f"{below}/{len(forecasts)} mã")
    report.note(f"Khoảng xác suất: {vi_num(min(probabilities), 3)} – {vi_num(max(probabilities), 3)}")

    report.section("Bước 2 — Dòng trạng thái và thông báo")
    retrieved = to_vn(snapshot.retrieved_at)
    report.line("retrieved_at", f"{retrieved:%H:%M} giờ VN ngày {retrieved:%d/%m/%Y}")
    for warning in data["warnings"]:
        report.note(f"> {warning}")

    top = forecasts[0]
    symbol = top["symbol"]
    report.section(f"Bước 3 — Bằng chứng của mã đứng đầu ({symbol})")
    for line in top["evidence"]:
        report.line("Bằng chứng", line)
    rows = market[market["symbol"] == symbol].sort_values("date")
    runs = [run for run in floor_runs(rows) if run[2] >= 2]
    for first, last, count, change in runs:
        report.note(f"Chuỗi {count} phiên open=high=low=close {first:%d/%m}–{last:%d/%m/%Y}: {vi_pct(change, signed=True)}")
    if runs:
        first, last, count, _ = max(runs, key=lambda run: run[2])
        closes = rows.set_index("date")["close"]
        before = closes[closes.index < first].iloc[-1]
        report.line(f"Chuỗi dài nhất", f"{count} phiên", f"{first:%d/%m}", f"{last:%d/%m/%Y}",
                    f"{vi_num(before, 2)} về {vi_num(closes[last], 2)}")
    report.note(f"Biến động 1 phiên lớn nhất: {vi_pct(rows['close'].pct_change().abs().max())} (biên độ HOSE ±7%)")

    model = BaselineModel(estimator=None).fit(train)
    logistic = getattr(model.estimator, "named_steps", {}).get("logisticregression")
    if logistic is not None:
        weights = sorted(zip(FEATURE_COLUMNS, logistic.coef_[0]), key=lambda pair: -abs(pair[1]))
        report.line(f"Hệ số chuẩn hóa lớn nhất ({weights[0][0]})", vi_num(weights[0][1], 2))
        report.note("Các hệ số khác: " + ", ".join(f"{name} {vi_num(weight, 2)}" for name, weight in weights[1:]))
    else:
        report.note("Mô hình rơi về DummyClassifier: không có hệ số.")

    drops = train[train["return_20d"] < BIG_DROP]
    report.line(f"Dòng huấn luyện giảm hơn {abs(BIG_DROP):.0%} trong 20 phiên",
                f"{len(drops)} dòng", f"{drops['symbol'].nunique()} mã",
                f"{(drops['symbol'] == symbol).sum()} dòng trong đó là chính {symbol}")
    if len(drops):
        report.line("Tỷ lệ tăng sau 20 phiên: nhóm đó / tỷ lệ nền",
                    vi_pct(drops["target_positive"].astype(int).mean()),
                    vi_pct(train["target_positive"].astype(int).mean()))
    volatility = {item["symbol"]: item["expected_volatility"] for item in forecasts}
    highest = max(volatility, key=volatility.get)
    banks = [value for name, value in volatility.items() if name in VN30_BANKS]
    report.line(f"Biến động cao nhất rổ ({highest})", vi_pct(volatility[highest], 1), vi_pct(volatility[highest], 2))
    if banks:
        report.line(f"Biến động trung bình {len(banks)} mã ngân hàng", vi_pct(float(np.mean(banks)), 1))

    report.section("Bước 4 — Danh sách nghiên cứu")
    symbols = [item["symbol"] for item in forecasts]
    for profile, label in PROFILES.items():
        result = client.post("/recommendations",
                             json={"confirmed": True, "risk_profile": profile, "symbols": symbols})
        report.line(label, f"| {label} | {', '.join(result.json()['symbols'])} |")
    report.line("Khoảng biến động trong rổ",
                f"{vi_pct(min(volatility.values()), 2)} đến {vi_pct(max(volatility.values()), 2)}")
    by_symbol = {item["symbol"]: item for item in forecasts}
    if "VCB" in by_symbol:
        report.line("VCB", f"VCB ({vi_pct(by_symbol['VCB']['positive_return_probability'])})")

    report.section("Mục 6 — Giới hạn")
    rows_per_symbol = train.groupby("symbol").size()
    report.line("Dòng huấn luyện mỗi mã (trung vị)", f"khoảng {int(rows_per_symbol.median())} dòng")
    first_close = market.sort_values("date").groupby("symbol")["close"].first()
    last_close = market.sort_values("date").groupby("symbol")["close"].last()
    report.line("Mua và giữ cả kỳ, trung vị các mã", f"trung vị {vi_pct(float((last_close / first_close - 1).median()))}")
    if args.skip_eval:
        report.note("(bỏ qua walk-forward: --skip-eval)")
    else:
        for params in ({}, {"min_train_rows": 1000, "test_window": 300}):
            result = walk_forward_evaluate(featured, **params)
            label = "mặc định" if not params else ", ".join(f"{k}={v}" for k, v in params.items())
            auc = vi_num(result.auc, 3) if result.auc is not None else "không tính được"
            report.line(f"Walk-forward ({label})", f"AUC {auc}", f"accuracy {vi_pct(result.accuracy)}")
            if not params:
                report.line("Lợi nhuận của hàm (chưa tin được, xem Issue #5)",
                            vi_pct(result.strategy_return, 0, signed=True), vi_pct(result.buy_and_hold_return, 0))

    print(f"\n{report.missing} giá trị không thấy nguyên văn trong {DEMO_DOC.relative_to(REPO)}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
