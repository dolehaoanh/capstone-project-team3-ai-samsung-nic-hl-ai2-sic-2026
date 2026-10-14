import pandas as pd
import pytest

from vietnam_stock_forecasting.data import MarketSnapshot, validate_market_frame, write_csv_snapshot


def test_missing_columns_are_reported():
    with pytest.raises(ValueError, match="missing required columns"):
        validate_market_frame(pd.DataFrame({"date": ["2024-01-01"]}))


def test_snapshot_writer_creates_reproducible_csv(tmp_path):
    frame = pd.DataFrame(
        {
            "date": pd.to_datetime(["2024-01-02"]),
            "symbol": ["AAA"],
            "open": [10],
            "high": [11],
            "low": [9],
            "close": [10.5],
            "volume": [100],
        }
    )
    output = write_csv_snapshot(
        MarketSnapshot(frame=frame, source="fixture", retrieved_at=pd.Timestamp.utcnow()),
        tmp_path / "nested" / "snapshot.csv",
    )
    assert output.is_file()
    assert pd.read_csv(output).iloc[0]["symbol"] == "AAA"
