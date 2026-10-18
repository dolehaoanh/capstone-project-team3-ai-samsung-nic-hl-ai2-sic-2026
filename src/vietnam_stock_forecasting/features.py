from __future__ import annotations

import pandas as pd


FEATURE_COLUMNS = [
    "return_5d",
    "return_20d",
    "volatility_20d",
    "volume_ratio_20d",
    "distance_from_ma20",
]


def build_features(market: pd.DataFrame, horizon_days: int = 20) -> pd.DataFrame:
    """Build features and a forward-return target without using future values as features."""
    if horizon_days <= 0:
        raise ValueError("horizon_days must be positive")
    required = {"date", "symbol", "close", "volume"}
    missing = required.difference(market.columns)
    if missing:
        raise ValueError(f"Feature input is missing columns: {sorted(missing)}")

    frame = market.sort_values(["symbol", "date"]).copy()
    grouped = frame.groupby("symbol", group_keys=False)
    frame["return_5d"] = grouped["close"].pct_change(5)
    frame["return_20d"] = grouped["close"].pct_change(20)
    frame["volatility_20d"] = grouped["close"].transform(
        lambda values: values.pct_change().rolling(20).std()
    )
    frame["volume_ratio_20d"] = frame["volume"] / grouped["volume"].transform(
        lambda values: values.rolling(20).mean()
    )
    moving_average = grouped["close"].transform(lambda values: values.rolling(20).mean())
    frame["distance_from_ma20"] = frame["close"] / moving_average - 1

    future_close = grouped["close"].shift(-horizon_days)
    frame["forward_return"] = future_close / frame["close"] - 1
    frame["target_positive"] = (
        (frame["forward_return"] > 0)
        .where(frame["forward_return"].notna(), pd.NA)
        .astype("Int64")
    )
    return frame
