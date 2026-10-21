"""Run the full baseline modeling and walk-forward evaluation pipeline.

Usage
-----
From the repo root:

    python scripts/run_modeling.py

Optional arguments:

    --snapshot PATH     Path to the OHLCV CSV snapshot (default: data/sample/vn30_snapshot.csv)
    --horizon INT       Forward-return horizon in trading days (default: 20)
    --min-train INT     Minimum rows in the initial training window (default: 250)
    --test-window INT   Rows per walk-forward test step (default: 30)
    --save-model PATH   Save the final model fitted on all data to this path
                        (default: models/baseline_v1.pkl)
    --no-save           Skip saving the final model

Example
-------
    python scripts/run_modeling.py --snapshot data/sample/vn30_snapshot.csv \\
        --horizon 20 --min-train 250 --test-window 30 --save-model models/baseline_v1.pkl
"""

from __future__ import annotations

import argparse
import json
import sys
import textwrap
from pathlib import Path

# Allow running from repo root without installing the package
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import pandas as pd

from vietnam_stock_forecasting.data import load_csv_snapshot
from vietnam_stock_forecasting.evaluation import (
    EvaluationResult,
    PerSymbolResult,
    walk_forward_evaluate_per_symbol,
)
from vietnam_stock_forecasting.features import build_features
from vietnam_stock_forecasting.model import BaselineModel


# ---------------------------------------------------------------------------
# Formatting helpers
# ---------------------------------------------------------------------------

_SEP = "-" * 64


def _fmt_pct(value: float, decimals: int = 2) -> str:
    return f"{value * 100:+.{decimals}f}%"


def _fmt_float(value: float, decimals: int = 4) -> str:
    return f"{value:.{decimals}f}"


def _print_aggregate(result: EvaluationResult) -> None:
    print(_SEP)
    print("  WALK-FORWARD EVALUATION — AGGREGATE")
    print(_SEP)
    rows = [
        ("Out-of-sample rows", str(result.rows)),
        ("Walk-forward windows", str(result.n_windows)),
        ("Dummy windows (single class)", str(result.dummy_windows)),
        ("", ""),
        ("Accuracy", _fmt_float(result.accuracy, 4)),
        ("Baseline accuracy (majority class)", _fmt_float(result.baseline_accuracy, 4)),
        ("Accuracy lift vs baseline", _fmt_pct(result.accuracy - result.baseline_accuracy, 2)),
        ("", ""),
        ("Brier score (lower = better)", _fmt_float(result.brier_score, 4)),
        ("ROC-AUC", _fmt_float(result.auc, 4) if result.auc is not None else "N/A"),
        ("", ""),
        ("Strategy return (long-only)", _fmt_pct(result.strategy_return)),
        ("Buy-and-hold return", _fmt_pct(result.buy_and_hold_return)),
        ("Max drawdown (strategy)", _fmt_pct(result.max_drawdown)),
    ]
    width = max(len(k) for k, _ in rows if k)
    for key, val in rows:
        if not key:
            print()
            continue
        print(f"  {key:<{width}}  {val}")
    print(_SEP)


def _print_per_symbol(per_symbol: list[PerSymbolResult]) -> None:
    print()
    print(_SEP)
    print("  PER-SYMBOL BREAKDOWN")
    print(_SEP)
    header = f"  {'Symbol':<8}  {'Rows':>5}  {'Acc':>6}  {'Base':>6}  {'Lift':>6}  {'AUC':>6}  {'Strat%':>8}  {'B&H%':>8}  {'MDD%':>8}"
    print(header)
    print("  " + "-" * (len(header) - 2))
    for r in sorted(per_symbol, key=lambda x: x.accuracy, reverse=True):
        auc_str = f"{r.auc:.4f}" if r.auc is not None else "  N/A "
        lift = r.accuracy - r.baseline_accuracy
        print(
            f"  {r.symbol:<8}  {r.rows:>5}  {r.accuracy:>6.4f}  "
            f"{r.baseline_accuracy:>6.4f}  {lift:>+6.4f}  {auc_str:>6}  "
            f"{r.strategy_return * 100:>+8.2f}  "
            f"{r.buy_and_hold_return * 100:>+8.2f}  "
            f"{r.max_drawdown * 100:>+8.2f}"
        )
    print(_SEP)


def _save_results_json(
    result: EvaluationResult,
    per_symbol: list[PerSymbolResult],
    path: Path,
) -> None:
    payload = {
        "aggregate": result.summary(),
        "per_symbol": [
            {
                "symbol": r.symbol,
                "rows": r.rows,
                "accuracy": round(r.accuracy, 4),
                "baseline_accuracy": round(r.baseline_accuracy, 4),
                "accuracy_lift": round(r.accuracy - r.baseline_accuracy, 4),
                "auc": round(r.auc, 4) if r.auc is not None else None,
                "strategy_return_pct": round(r.strategy_return * 100, 2),
                "buy_and_hold_return_pct": round(r.buy_and_hold_return * 100, 2),
                "max_drawdown_pct": round(r.max_drawdown * 100, 2),
            }
            for r in per_symbol
        ],
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, ensure_ascii=False)
        f.write("\n")
    print(f"\n  Results saved → {path}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=textwrap.dedent("""\
            Run baseline + walk-forward evaluation for Vietnam equity forecasting.
        """),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--snapshot",
        default="data/sample/vn30_snapshot.csv",
        help="OHLCV CSV snapshot path (default: data/sample/vn30_snapshot.csv)",
    )
    parser.add_argument(
        "--horizon",
        type=int,
        default=20,
        help="Forward-return horizon in trading days (default: 20)",
    )
    parser.add_argument(
        "--min-train",
        type=int,
        default=250,
        dest="min_train",
        help="Minimum rows in the initial training window (default: 250)",
    )
    parser.add_argument(
        "--test-window",
        type=int,
        default=30,
        dest="test_window",
        help="Rows per walk-forward test step (default: 30)",
    )
    parser.add_argument(
        "--save-model",
        default="models/baseline_v1.pkl",
        dest="save_model",
        help="Where to save the final model (default: models/baseline_v1.pkl)",
    )
    parser.add_argument(
        "--no-save",
        action="store_true",
        help="Skip saving the final model",
    )
    parser.add_argument(
        "--results-json",
        default="models/evaluation_results.json",
        dest="results_json",
        help="Where to save evaluation results as JSON (default: models/evaluation_results.json)",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)

    print()
    print(_SEP)
    print("  VIETNAM STOCK FORECASTING — BASELINE MODELING")
    print(_SEP)

    # ── 1. Load snapshot ─────────────────────────────────────────────────────
    snapshot_path = Path(args.snapshot)
    print(f"\n  [1/4] Loading snapshot: {snapshot_path}")
    snapshot = load_csv_snapshot(snapshot_path)
    print(
        f"        {len(snapshot.frame):,} rows | "
        f"{len(snapshot.frame['symbol'].unique())} symbols | "
        f"{snapshot.start_date} -> {snapshot.end_date}"
    )

    # ── 2. Build features ────────────────────────────────────────────────────
    print(f"\n  [2/4] Building features (horizon={args.horizon}d) …")
    featured = build_features(snapshot.frame, horizon_days=args.horizon)
    clean = featured.dropna(
        subset=["return_5d", "return_20d", "volatility_20d",
                "volume_ratio_20d", "distance_from_ma20",
                "target_positive", "forward_return"]
    )
    print(f"        {len(featured):,} rows → {len(clean):,} complete rows after NaN removal")
    pos_rate = clean["target_positive"].astype(int).mean()
    print(f"        Label balance: {pos_rate:.1%} positive, {1 - pos_rate:.1%} non-positive")

    # ── 3. Walk-forward evaluation ───────────────────────────────────────────
    print(
        f"\n  [3/4] Walk-forward evaluation "
        f"(min_train={args.min_train}, test_window={args.test_window}) …"
    )
    try:
        aggregate, per_symbol = walk_forward_evaluate_per_symbol(
            featured,
            min_train_rows=args.min_train,
            test_window=args.test_window,
        )
    except ValueError as exc:
        print(f"\n  ERROR: {exc}", file=sys.stderr)
        return 1

    _print_aggregate(aggregate)
    _print_per_symbol(per_symbol)

    # Save JSON results
    _save_results_json(aggregate, per_symbol, Path(args.results_json))

    # ── 4. Fit final model on full data & save ───────────────────────────────
    if not args.no_save:
        print(f"\n  [4/4] Fitting final model on all {len(clean):,} complete rows …")
        final_model = BaselineModel(estimator=None).fit(featured)
        if final_model.is_dummy:
            print(
                "  WARNING: Training data has only one class — "
                "final model is a DummyClassifier."
            )
        model_path = Path(args.save_model)
        final_model.save(model_path)
        print(f"  Model saved → {model_path}")
        print(f"  Metadata   → {model_path.with_suffix('.metadata.json')}")
    else:
        print("\n  [4/4] --no-save flag set; skipping model serialisation.")

    print()
    print(_SEP)
    print("  Done.")
    print(_SEP)
    print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
