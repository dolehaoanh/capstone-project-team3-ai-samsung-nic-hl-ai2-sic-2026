from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd
from sklearn.metrics import accuracy_score, brier_score_loss, roc_auc_score

from .features import FEATURE_COLUMNS
from .model import BaselineModel


@dataclass(frozen=True)
class EvaluationResult:
    rows: int
    accuracy: float
    brier_score: float
    auc: float | None
    strategy_return: float
    buy_and_hold_return: float
    max_drawdown: float


def walk_forward_evaluate(
    frame: pd.DataFrame,
    *,
    min_train_rows: int = 30,
    test_window: int = 5,
) -> EvaluationResult:
    """Evaluate only on future rows relative to each training window."""
    clean = frame.dropna(subset=FEATURE_COLUMNS + ["target_positive", "forward_return"])
    clean = clean.sort_values(["date", "symbol"]).reset_index(drop=True)
    if len(clean) <= min_train_rows:
        raise ValueError("Not enough rows for walk-forward evaluation")

    probabilities: list[float] = []
    actuals: list[int] = []
    returns: list[float] = []
    start = min_train_rows
    while start < len(clean):
        stop = min(start + test_window, len(clean))
        train = clean.iloc[:start]
        test = clean.iloc[start:stop]
        model = BaselineModel(estimator=None).fit(train)
        probabilities.extend(model.predict_probability(test))
        actuals.extend(test["target_positive"].astype(int).tolist())
        returns.extend(test["forward_return"].astype(float).tolist())
        start = stop

    predictions = np.array(probabilities)
    actual = np.array(actuals)
    realized = np.array(returns)
    strategy = np.where(predictions >= 0.5, realized, 0.0)
    equity = np.cumprod(1 + strategy)
    running_max = np.maximum.accumulate(equity)
    drawdowns = equity / running_max - 1
    auc = float(roc_auc_score(actual, predictions)) if len(np.unique(actual)) > 1 else None
    return EvaluationResult(
        rows=len(actual),
        accuracy=float(accuracy_score(actual, predictions >= 0.5)),
        brier_score=float(brier_score_loss(actual, predictions)),
        auc=auc,
        strategy_return=float(np.prod(1 + strategy) - 1),
        buy_and_hold_return=float(np.prod(1 + realized) - 1),
        max_drawdown=float(drawdowns.min()),
    )
