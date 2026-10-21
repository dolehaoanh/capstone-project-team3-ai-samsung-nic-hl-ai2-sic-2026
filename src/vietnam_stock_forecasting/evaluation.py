"""Walk-forward evaluation for the baseline model.

All metrics are computed on out-of-sample rows only: each test window is
evaluated on a model trained exclusively on rows that precede it in time.
No future prices ever enter a training set.

Lane: Modeling (Issue #5).
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd
from sklearn.metrics import accuracy_score, brier_score_loss, roc_auc_score

from .features import FEATURE_COLUMNS
from .model import BaselineModel


# ---------------------------------------------------------------------------
# Result containers
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class EvaluationResult:
    """Aggregate walk-forward metrics across all test windows.

    Attributes:
        rows: Total out-of-sample rows evaluated.
        n_windows: Number of walk-forward splits used.
        accuracy: Fraction of correct binary direction calls (threshold 0.5).
        baseline_accuracy: Majority-class accuracy (best trivial strategy).
        brier_score: Mean squared error of probability forecasts; lower = better.
        auc: ROC-AUC.  ``None`` when only one class appears in the test set.
        strategy_return: Compounded return of a strategy that goes long when
            ``P(positive) >= 0.5`` and holds cash otherwise.
        buy_and_hold_return: Compounded return of holding every asset in the
            test window regardless of the model's opinion.
        max_drawdown: Worst peak-to-trough drawdown of the model's equity curve;
            always <= 0.
        dummy_windows: Number of splits where the training set collapsed to a
            single class (``DummyClassifier`` was used).
    """

    rows: int
    n_windows: int
    accuracy: float
    baseline_accuracy: float
    brier_score: float
    auc: float | None
    strategy_return: float
    buy_and_hold_return: float
    max_drawdown: float
    dummy_windows: int = 0

    def summary(self) -> dict[str, object]:
        """Return a plain-dict representation for logging / JSON serialisation."""
        return {
            "rows": self.rows,
            "n_windows": self.n_windows,
            "accuracy": round(self.accuracy, 4),
            "baseline_accuracy": round(self.baseline_accuracy, 4),
            "accuracy_lift": round(self.accuracy - self.baseline_accuracy, 4),
            "brier_score": round(self.brier_score, 4),
            "auc": round(self.auc, 4) if self.auc is not None else None,
            "strategy_return_pct": round(self.strategy_return * 100, 2),
            "buy_and_hold_return_pct": round(self.buy_and_hold_return * 100, 2),
            "max_drawdown_pct": round(self.max_drawdown * 100, 2),
            "dummy_windows": self.dummy_windows,
        }


@dataclass(frozen=True)
class PerSymbolResult:
    """Per-symbol breakdown of walk-forward metrics.

    Attributes:
        symbol: Stock ticker.
        rows: Out-of-sample rows for this symbol.
        accuracy: Direction accuracy.
        baseline_accuracy: Majority-class accuracy for this symbol.
        auc: ROC-AUC or ``None``.
        strategy_return: Compounded strategy return.
        buy_and_hold_return: Compounded buy-and-hold return.
        max_drawdown: Worst drawdown of this symbol's equity curve.
    """

    symbol: str
    rows: int
    accuracy: float
    baseline_accuracy: float
    auc: float | None
    strategy_return: float
    buy_and_hold_return: float
    max_drawdown: float


# ---------------------------------------------------------------------------
# Walk-forward evaluation — pooled (cross-symbol, sorted by date)
# ---------------------------------------------------------------------------


def walk_forward_evaluate(
    frame: pd.DataFrame,
    *,
    min_train_rows: int = 30,
    test_window: int = 5,
) -> EvaluationResult:
    """Evaluate the baseline model on *frame* using a pooled walk-forward scheme.

    All symbols are pooled and sorted by date.  At each step the model is
    trained on rows **before** position *start* and evaluated on the next
    *test_window* rows.  This mirrors how a practitioner would retrain the
    model at regular intervals.

    Args:
        frame: Feature-enriched DataFrame from ``build_features``.
        min_train_rows: Minimum number of complete rows required in the initial
            training window.  Evaluation only starts after this many rows.
        test_window: Number of rows to include in each test step.

    Returns:
        :class:`EvaluationResult` with aggregate out-of-sample metrics.

    Raises:
        ValueError: If there are not enough rows after cleaning.
    """
    clean = frame.dropna(subset=FEATURE_COLUMNS + ["target_positive", "forward_return"])
    clean = clean.sort_values(["date", "symbol"]).reset_index(drop=True)

    if len(clean) <= min_train_rows:
        raise ValueError(
            f"Not enough rows for walk-forward evaluation: "
            f"got {len(clean)}, need > {min_train_rows}"
        )

    probabilities: list[float] = []
    actuals: list[int] = []
    returns: list[float] = []
    dummy_windows: int = 0

    start = min_train_rows
    while start < len(clean):
        stop = min(start + test_window, len(clean))
        train = clean.iloc[:start]
        test = clean.iloc[start:stop]

        model = BaselineModel(estimator=None).fit(train)
        if model.is_dummy:
            dummy_windows += 1

        probabilities.extend(model.predict_probability(test).tolist())
        actuals.extend(test["target_positive"].astype(int).tolist())
        returns.extend(test["forward_return"].astype(float).tolist())
        start = stop

    return _aggregate(
        probabilities=probabilities,
        actuals=actuals,
        returns=returns,
        n_windows=(len(clean) - min_train_rows + test_window - 1) // test_window,
        dummy_windows=dummy_windows,
    )


# ---------------------------------------------------------------------------
# Per-symbol walk-forward evaluation
# ---------------------------------------------------------------------------


def walk_forward_evaluate_per_symbol(
    frame: pd.DataFrame,
    *,
    min_train_rows: int = 30,
    test_window: int = 5,
) -> tuple[EvaluationResult, list[PerSymbolResult]]:
    """Run pooled walk-forward evaluation and also break down results by symbol.

    Training is always pooled (all symbols together), which mirrors production
    usage.  The per-symbol breakdown is computed by filtering the collected
    out-of-sample rows after evaluation; it does **not** change the training
    procedure.

    Args:
        frame: Feature-enriched DataFrame from ``build_features``.
        min_train_rows: Passed to the pooled evaluator.
        test_window: Passed to the pooled evaluator.

    Returns:
        ``(aggregate_result, per_symbol_results)`` where *per_symbol_results*
        is a list of :class:`PerSymbolResult`, one per symbol with at least
        one out-of-sample row.
    """
    clean = frame.dropna(subset=FEATURE_COLUMNS + ["target_positive", "forward_return"])
    clean = clean.sort_values(["date", "symbol"]).reset_index(drop=True)

    if len(clean) <= min_train_rows:
        raise ValueError(
            f"Not enough rows for walk-forward evaluation: "
            f"got {len(clean)}, need > {min_train_rows}"
        )

    # Collect (symbol, probability, actual, realized_return) per test row
    records: list[dict[str, object]] = []
    dummy_windows: int = 0

    start = min_train_rows
    n_windows = 0
    while start < len(clean):
        stop = min(start + test_window, len(clean))
        train = clean.iloc[:start]
        test = clean.iloc[start:stop]

        model = BaselineModel(estimator=None).fit(train)
        if model.is_dummy:
            dummy_windows += 1

        probs = model.predict_probability(test)
        for i, (idx, row) in enumerate(test.iterrows()):
            records.append(
                {
                    "symbol": str(row["symbol"]),
                    "probability": float(probs[i]),
                    "actual": int(row["target_positive"]),
                    "forward_return": float(row["forward_return"]),
                }
            )
        start = stop
        n_windows += 1

    if not records:
        raise ValueError("No out-of-sample records produced")

    rec_df = pd.DataFrame(records)

    # Aggregate result
    aggregate = _aggregate(
        probabilities=rec_df["probability"].tolist(),
        actuals=rec_df["actual"].tolist(),
        returns=rec_df["forward_return"].tolist(),
        n_windows=n_windows,
        dummy_windows=dummy_windows,
    )

    # Per-symbol results
    per_symbol: list[PerSymbolResult] = []
    for sym, grp in rec_df.groupby("symbol", sort=True):
        probs_s = grp["probability"].to_numpy()
        act_s = grp["actual"].to_numpy()
        ret_s = grp["forward_return"].to_numpy()

        baseline_acc = float(max(act_s.mean(), 1 - act_s.mean()))
        auc_s: float | None = None
        if len(np.unique(act_s)) > 1:
            try:
                auc_s = float(roc_auc_score(act_s, probs_s))
            except Exception:
                pass

        strategy_s = np.where(probs_s >= 0.5, ret_s, 0.0)
        equity_s = np.cumprod(1 + strategy_s)
        running_max_s = np.maximum.accumulate(equity_s)
        drawdowns_s = equity_s / running_max_s - 1

        per_symbol.append(
            PerSymbolResult(
                symbol=str(sym),
                rows=len(grp),
                accuracy=float(accuracy_score(act_s, probs_s >= 0.5)),
                baseline_accuracy=baseline_acc,
                auc=auc_s,
                strategy_return=float(np.prod(1 + strategy_s) - 1),
                buy_and_hold_return=float(np.prod(1 + ret_s) - 1),
                max_drawdown=float(drawdowns_s.min()),
            )
        )

    return aggregate, per_symbol


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _aggregate(
    *,
    probabilities: list[float],
    actuals: list[int],
    returns: list[float],
    n_windows: int,
    dummy_windows: int,
) -> EvaluationResult:
    predictions = np.array(probabilities)
    actual = np.array(actuals)
    realized = np.array(returns)

    strategy = np.where(predictions >= 0.5, realized, 0.0)
    equity = np.cumprod(1 + strategy)
    running_max = np.maximum.accumulate(equity)
    drawdowns = equity / running_max - 1

    auc: float | None = None
    if len(np.unique(actual)) > 1:
        try:
            auc = float(roc_auc_score(actual, predictions))
        except Exception:
            pass

    majority_class_rate = float(max(actual.mean(), 1 - actual.mean()))

    return EvaluationResult(
        rows=len(actual),
        n_windows=n_windows,
        accuracy=float(accuracy_score(actual, predictions >= 0.5)),
        baseline_accuracy=majority_class_rate,
        brier_score=float(brier_score_loss(actual, predictions)),
        auc=auc,
        strategy_return=float(np.prod(1 + strategy) - 1),
        buy_and_hold_return=float(np.prod(1 + realized) - 1),
        max_drawdown=float(drawdowns.min()),
        dummy_windows=dummy_windows,
    )
