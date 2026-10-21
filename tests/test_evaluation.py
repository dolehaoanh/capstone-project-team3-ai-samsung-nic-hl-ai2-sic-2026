"""Tests for evaluation.py — walk-forward evaluation (pooled and per-symbol).

All tests run offline with synthetic fixtures.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from vietnam_stock_forecasting.evaluation import (
    EvaluationResult,
    PerSymbolResult,
    walk_forward_evaluate,
    walk_forward_evaluate_per_symbol,
)
from vietnam_stock_forecasting.features import FEATURE_COLUMNS, build_features


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_featured(n: int = 120, symbols: list[str] | None = None) -> pd.DataFrame:
    syms = symbols or ["AAA", "BBB"]
    frames = []
    for i, sym in enumerate(syms):
        base = 100 + i * 10
        dates = pd.date_range("2024-01-01", periods=n)
        close = [base + j + (j % 5) * (1 if i == 0 else -1) for j in range(n)]
        frames.append(
            pd.DataFrame(
                {
                    "date": dates,
                    "symbol": sym,
                    "open": [c * 0.99 for c in close],
                    "high": [c * 1.01 for c in close],
                    "low": [c * 0.98 for c in close],
                    "close": close,
                    "volume": [1_000 + j * 5 for j in range(n)],
                }
            )
        )
    market = pd.concat(frames, ignore_index=True)
    return build_features(market, horizon_days=20)


@pytest.fixture()
def featured() -> pd.DataFrame:
    return _make_featured(n=120)


# ---------------------------------------------------------------------------
# walk_forward_evaluate (pooled)
# ---------------------------------------------------------------------------


class TestWalkForwardEvaluate:
    def test_returns_evaluation_result(self, featured: pd.DataFrame) -> None:
        result = walk_forward_evaluate(featured, min_train_rows=30, test_window=5)
        assert isinstance(result, EvaluationResult)

    def test_rows_positive(self, featured: pd.DataFrame) -> None:
        result = walk_forward_evaluate(featured, min_train_rows=30, test_window=5)
        assert result.rows > 0

    def test_accuracy_in_unit_interval(self, featured: pd.DataFrame) -> None:
        result = walk_forward_evaluate(featured, min_train_rows=30, test_window=5)
        assert 0.0 <= result.accuracy <= 1.0

    def test_brier_score_non_negative(self, featured: pd.DataFrame) -> None:
        result = walk_forward_evaluate(featured, min_train_rows=30, test_window=5)
        assert result.brier_score >= 0.0

    def test_max_drawdown_non_positive(self, featured: pd.DataFrame) -> None:
        result = walk_forward_evaluate(featured, min_train_rows=30, test_window=5)
        assert result.max_drawdown <= 0.0

    def test_baseline_accuracy_geq_half(self, featured: pd.DataFrame) -> None:
        """Majority-class baseline accuracy must be >= 50 % by definition."""
        result = walk_forward_evaluate(featured, min_train_rows=30, test_window=5)
        assert result.baseline_accuracy >= 0.5

    def test_raises_when_not_enough_rows(self) -> None:
        tiny = _make_featured(n=30, symbols=["AAA"])
        with pytest.raises(ValueError, match="Not enough rows"):
            walk_forward_evaluate(tiny, min_train_rows=200, test_window=5)

    def test_no_future_data_leaks_into_training(self) -> None:
        """The test window must only use rows that come AFTER the training set."""
        # Build a marker: close increases by 1 each day; forward_return is deterministic.
        market = pd.DataFrame(
            {
                "date": pd.date_range("2024-01-01", periods=100),
                "symbol": "ZZZ",
                "close": [100 + i for i in range(100)],
                "volume": [1000] * 100,
            }
        )
        featured_single = build_features(market, horizon_days=5)
        clean = featured_single.dropna(
            subset=FEATURE_COLUMNS + ["target_positive", "forward_return"]
        )
        # Dates in the test window must all be strictly after the last training date.
        clean_sorted = clean.sort_values(["date", "symbol"]).reset_index(drop=True)
        min_train = 30
        test_window = 5
        start = min_train
        while start < len(clean_sorted):
            stop = min(start + test_window, len(clean_sorted))
            last_train_date = clean_sorted.iloc[start - 1]["date"]
            first_test_date = clean_sorted.iloc[start]["date"]
            assert first_test_date > last_train_date, (
                f"Leakage detected: train ends on {last_train_date}, "
                f"test starts on {first_test_date}"
            )
            start = stop

    def test_summary_dict_has_expected_keys(self, featured: pd.DataFrame) -> None:
        result = walk_forward_evaluate(featured, min_train_rows=30, test_window=5)
        summary = result.summary()
        for key in [
            "rows", "n_windows", "accuracy", "baseline_accuracy",
            "accuracy_lift", "brier_score", "auc",
            "strategy_return_pct", "buy_and_hold_return_pct", "max_drawdown_pct",
            "dummy_windows",
        ]:
            assert key in summary, f"Missing key in summary(): '{key}'"


# ---------------------------------------------------------------------------
# walk_forward_evaluate_per_symbol
# ---------------------------------------------------------------------------


class TestWalkForwardEvaluatePerSymbol:
    def test_returns_tuple_of_aggregate_and_list(self, featured: pd.DataFrame) -> None:
        aggregate, per_symbol = walk_forward_evaluate_per_symbol(
            featured, min_train_rows=30, test_window=5
        )
        assert isinstance(aggregate, EvaluationResult)
        assert isinstance(per_symbol, list)

    def test_per_symbol_covers_all_symbols(self, featured: pd.DataFrame) -> None:
        clean = featured.dropna(
            subset=FEATURE_COLUMNS + ["target_positive", "forward_return"]
        )
        expected_symbols = set(clean["symbol"].unique())
        _, per_symbol = walk_forward_evaluate_per_symbol(
            featured, min_train_rows=30, test_window=5
        )
        returned_symbols = {r.symbol for r in per_symbol}
        assert returned_symbols == expected_symbols

    def test_per_symbol_rows_sum_to_aggregate_rows(self, featured: pd.DataFrame) -> None:
        aggregate, per_symbol = walk_forward_evaluate_per_symbol(
            featured, min_train_rows=30, test_window=5
        )
        assert sum(r.rows for r in per_symbol) == aggregate.rows

    def test_per_symbol_accuracy_in_unit_interval(self, featured: pd.DataFrame) -> None:
        _, per_symbol = walk_forward_evaluate_per_symbol(
            featured, min_train_rows=30, test_window=5
        )
        for r in per_symbol:
            assert 0.0 <= r.accuracy <= 1.0, f"{r.symbol}: accuracy out of range"

    def test_per_symbol_max_drawdown_non_positive(self, featured: pd.DataFrame) -> None:
        _, per_symbol = walk_forward_evaluate_per_symbol(
            featured, min_train_rows=30, test_window=5
        )
        for r in per_symbol:
            assert r.max_drawdown <= 0.0, f"{r.symbol}: max_drawdown positive"

    def test_isolation_between_symbols(self) -> None:
        """Features for one symbol must not bleed into another."""
        # Two symbols with completely different price series.
        market = pd.DataFrame(
            {
                "date": list(pd.date_range("2024-01-01", periods=100)) * 2,
                "symbol": ["SYM1"] * 100 + ["SYM2"] * 100,
                "close": [100 + i for i in range(100)] + [200 - i for i in range(100)],
                "volume": [1000] * 200,
            }
        )
        featured_two = build_features(market, horizon_days=5)
        _, per_symbol = walk_forward_evaluate_per_symbol(
            featured_two, min_train_rows=30, test_window=5
        )
        symbols = [r.symbol for r in per_symbol]
        assert "SYM1" in symbols
        assert "SYM2" in symbols

    def test_per_symbol_result_is_frozen_dataclass(self, featured: pd.DataFrame) -> None:
        _, per_symbol = walk_forward_evaluate_per_symbol(
            featured, min_train_rows=30, test_window=5
        )
        r = per_symbol[0]
        with pytest.raises((AttributeError, TypeError)):
            r.accuracy = 0.99  # type: ignore[misc]

    def test_aggregate_matches_pooled_walk_forward(self, featured: pd.DataFrame) -> None:
        """Per-symbol function's aggregate must equal the standalone pooled result."""
        agg_pooled = walk_forward_evaluate(featured, min_train_rows=30, test_window=5)
        agg_per, _ = walk_forward_evaluate_per_symbol(
            featured, min_train_rows=30, test_window=5
        )
        assert agg_pooled.rows == agg_per.rows
        assert abs(agg_pooled.accuracy - agg_per.accuracy) < 1e-9
        assert abs(agg_pooled.brier_score - agg_per.brier_score) < 1e-9
