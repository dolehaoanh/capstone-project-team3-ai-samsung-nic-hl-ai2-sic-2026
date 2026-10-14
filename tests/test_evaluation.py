import pandas as pd

from vietnam_stock_forecasting.evaluation import walk_forward_evaluate
from vietnam_stock_forecasting.features import build_features


def test_walk_forward_evaluation_does_not_use_future_test_rows_for_training():
    market = pd.DataFrame(
        {
            "date": pd.date_range("2024-01-01", periods=80),
            "symbol": ["AAA"] * 80,
            "close": [100 + i + (i % 3) for i in range(80)],
            "volume": [1000 + i * 10 for i in range(80)],
        }
    )
    result = walk_forward_evaluate(build_features(market), min_train_rows=30, test_window=5)
    assert result.rows > 0
    assert result.brier_score >= 0
    assert result.max_drawdown <= 0
