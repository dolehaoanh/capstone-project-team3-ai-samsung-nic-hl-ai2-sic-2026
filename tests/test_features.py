import pandas as pd
import pytest

from vietnam_stock_forecasting.features import build_features


def test_features_are_grouped_by_symbol_and_target_uses_future_rows():
    frame = pd.DataFrame(
        {
            "date": pd.date_range("2024-01-01", periods=25),
            "symbol": ["AAA"] * 25,
            "close": range(100, 125),
            "volume": [1000] * 25,
        }
    )
    result = build_features(frame, horizon_days=5)
    assert result.loc[0, "return_5d"] != result.loc[24, "return_5d"]
    assert not pd.isna(result.loc[19, "forward_return"])
    assert pd.isna(result.loc[24, "forward_return"])


def test_invalid_horizon_is_rejected():
    with pytest.raises(ValueError, match="horizon_days"):
        build_features(pd.DataFrame(), horizon_days=0)
