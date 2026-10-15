from unittest.mock import MagicMock, patch
import json
import pandas as pd
import pytest

from vietnam_stock_forecasting.data import (
    COLUMN_ORDER,
    MarketSnapshot,
    cli_main,
    fetch_vietnam_history,
    get_vn30_symbols,
    load_csv_snapshot,
    validate_market_frame,
    write_csv_snapshot,
)


def _valid_row(**kwargs):
    row = {
        "date": "2024-01-02",
        "symbol": "AAA",
        "open": 10.0,
        "high": 11.0,
        "low": 9.0,
        "close": 10.5,
        "volume": 1000.0,
    }
    row.update(kwargs)
    return row


def test_missing_columns_are_reported():
    with pytest.raises(ValueError, match="missing required columns"):
        validate_market_frame(pd.DataFrame({"date": ["2024-01-01"]}))


def test_empty_dataframe_raises():
    with pytest.raises(ValueError, match="at least one row"):
        validate_market_frame(pd.DataFrame(columns=COLUMN_ORDER))


def test_missing_date_values_raise():
    data = pd.DataFrame([_valid_row(date=None)])
    with pytest.raises(ValueError, match="missing date values"):
        validate_market_frame(data)


def test_invalid_date_values_raise():
    data = pd.DataFrame([_valid_row(date="not-a-valid-date")])
    with pytest.raises(ValueError, match="invalid date values"):
        validate_market_frame(data)


def test_missing_or_empty_symbol_raises():
    data_empty = pd.DataFrame([_valid_row(symbol="   ")])
    with pytest.raises(ValueError, match="missing or empty symbol values"):
        validate_market_frame(data_empty)

    data_none = pd.DataFrame([_valid_row(symbol=None)])
    with pytest.raises(ValueError, match="missing or empty symbol values"):
        validate_market_frame(data_none)


def test_missing_numeric_values_raise():
    data = pd.DataFrame([_valid_row(open=None)])
    with pytest.raises(ValueError, match="missing numeric values"):
        validate_market_frame(data)


def test_negative_price_or_volume_raises():
    data_price = pd.DataFrame([_valid_row(close=-1.0)])
    with pytest.raises(ValueError, match="cannot be negative"):
        validate_market_frame(data_price)

    data_volume = pd.DataFrame([_valid_row(volume=-50.0)])
    with pytest.raises(ValueError, match="cannot be negative"):
        validate_market_frame(data_volume)


def test_invalid_price_range_high_lower_than_low_raises():
    data = pd.DataFrame([_valid_row(high=8.0, low=10.0)])
    with pytest.raises(ValueError, match="high cannot be less than low"):
        validate_market_frame(data)


def test_duplicate_symbol_and_date_raises():
    data = pd.DataFrame([_valid_row(), _valid_row()])
    with pytest.raises(ValueError, match="duplicate symbol and date entries"):
        validate_market_frame(data)


def test_snapshot_writer_creates_reproducible_csv_and_metadata(tmp_path):
    frame = pd.DataFrame(
        [
            _valid_row(symbol="AAA", date="2024-01-02"),
            _valid_row(symbol="AAA", date="2024-01-03"),
            _valid_row(symbol="BBB", date="2024-01-02"),
        ]
    )
    frame["date"] = pd.to_datetime(frame["date"])
    snapshot = MarketSnapshot(frame=frame, source="fixture", retrieved_at=pd.Timestamp("2024-01-04 12:00:00"))

    assert snapshot.start_date == "2024-01-02"
    assert snapshot.end_date == "2024-01-03"
    assert snapshot.symbols == ("AAA", "BBB")

    output_csv = tmp_path / "nested" / "snapshot.csv"
    output = write_csv_snapshot(snapshot, output_csv)

    assert output.is_file()
    meta_path = output.with_suffix(".metadata.json")
    assert meta_path.is_file()

    with meta_path.open("r", encoding="utf-8") as f:
        meta = json.load(f)
    assert meta["source"] == "fixture"
    assert meta["start_date"] == "2024-01-02"
    assert meta["end_date"] == "2024-01-03"
    assert meta["symbols"] == ["AAA", "BBB"]
    assert meta["row_count"] == 3

    # Load back using load_csv_snapshot
    loaded = load_csv_snapshot(output)
    assert loaded.source == "fixture"
    assert loaded.symbols == ("AAA", "BBB")
    assert loaded.start_date == "2024-01-02"
    assert loaded.end_date == "2024-01-03"
    assert len(loaded.frame) == 3


def test_load_csv_snapshot_without_metadata(tmp_path):
    frame = pd.DataFrame([_valid_row(symbol="XYZ", date="2024-02-01")])
    frame["date"] = pd.to_datetime(frame["date"])
    output_csv = tmp_path / "raw_no_meta.csv"
    frame.to_csv(output_csv, index=False)

    loaded = load_csv_snapshot(output_csv)
    assert loaded.symbols == ("XYZ",)
    assert loaded.start_date == "2024-02-01"
    assert loaded.end_date == "2024-02-01"


def test_get_vn30_symbols():
    symbols = get_vn30_symbols(online=False)
    assert len(symbols) == 30
    assert "FPT" in symbols
    assert "VCB" in symbols
    assert "HPG" in symbols


def test_fetch_vietnam_history_offline_with_fixture_mock():
    mock_history = pd.DataFrame(
        {
            "time": ["2024-01-02 07:00:00+07:00", "2024-01-03 07:00:00+07:00"],
            "open": [10.0, 10.5],
            "high": [11.0, 11.5],
            "low": [9.5, 10.0],
            "close": [10.5, 11.0],
            "volume": [1000, 1200],
        }
    )

    class MockQuote:
        def __init__(self, symbol, source="VCI"):
            self.symbol = symbol
            self.source = source

        def history(self, start, end):
            return mock_history.copy()

    with patch("vnstock.Quote", MockQuote):
        snapshot = fetch_vietnam_history(["FPT"], start="2024-01-01", end="2024-01-05", source="VCI")
        assert len(snapshot.frame) == 2
        assert list(snapshot.frame.columns) == COLUMN_ORDER
        assert snapshot.frame.iloc[0]["symbol"] == "FPT"
        assert snapshot.source == "vnstock:VCI"
        assert snapshot.start_date == "2024-01-02"
        assert snapshot.end_date == "2024-01-03"
        assert snapshot.symbols == ("FPT",)


def test_fetch_vietnam_history_invalid_symbols_rejected():
    with pytest.raises(ValueError, match="At least one symbol is required"):
        fetch_vietnam_history([], start="2024-01-01", end="2024-01-05")

    with pytest.raises(ValueError, match="Invalid symbol"):
        fetch_vietnam_history(["  FPT  "], start="2024-01-01", end="2024-01-05")

    with pytest.raises(ValueError, match="Invalid symbol"):
        fetch_vietnam_history([""], start="2024-01-01", end="2024-01-05")


def test_cli_main_with_mock(tmp_path):
    mock_history = pd.DataFrame(
        {
            "time": ["2024-01-02 07:00:00+07:00"],
            "open": [10.0],
            "high": [11.0],
            "low": [9.0],
            "close": [10.5],
            "volume": [1000],
        }
    )

    class MockQuote:
        def __init__(self, symbol, source="VCI"):
            self.symbol = symbol

        def history(self, start, end):
            return mock_history.copy()

    out_csv = tmp_path / "cli_test.csv"
    with patch("vnstock.Quote", MockQuote):
        code = cli_main(["--symbols", "FPT", "--start", "2024-01-01", "--end", "2024-01-05", "--output", str(out_csv)])
        assert code == 0
        assert out_csv.is_file()
        assert out_csv.with_suffix(".metadata.json").is_file()
