from datetime import date, datetime, timedelta, timezone
import json
import os
from pathlib import Path

import pandas as pd
import pytest
from fastapi.testclient import TestClient

from vietnam_stock_forecasting.api import VN_TIMEZONE, _cached_forecasts, _rank_for_profile, app
from vietnam_stock_forecasting.contracts import Forecast, RiskProfile


client = TestClient(app)


@pytest.fixture(autouse=True)
def _no_background_refresh(monkeypatch, tmp_path):
    # Tests must stay offline and must never write the live snapshot inside the repository.
    monkeypatch.setenv("ALLOW_ONLINE_REFRESH", "false")
    monkeypatch.setenv("LIVE_SNAPSHOT_PATH", str(tmp_path / "raw" / "vn30_live.csv"))


def _closes(pattern: str, periods: int, offset: int) -> list[float]:
    """Price paths chosen for what they do to `target_positive`.

    "cycle" rises and falls over a 40-session period, so 20-session forward returns take
    both signs and the model trains a real LogisticRegression. "trend" only rises, so
    every label is 1 and BaselineModel falls back to DummyClassifier.
    """
    base = 100 + offset * 50
    if pattern == "trend":
        return [base + index + (index % 7) for index in range(periods)]
    return [base + abs((index % 40) - 20) * 2 + (index % 5) for index in range(periods)]


def _write_snapshot(
    path: Path,
    *,
    symbols=("AAA", "BBB"),
    periods: int = 80,
    pattern: str = "cycle",
    end: str | None = None,
) -> Path:
    """Write a small multi-symbol snapshot that passes validate_market_frame."""
    dates = (
        pd.date_range(end=end, periods=periods)
        if end
        else pd.date_range("2024-01-01", periods=periods)
    )
    frames = []
    for offset, symbol in enumerate(symbols):
        close = _closes(pattern, periods, offset)
        frames.append(
            pd.DataFrame(
                {
                    "date": dates,
                    "symbol": symbol,
                    "open": close,
                    "high": [value + 1 for value in close],
                    "low": [value - 1 for value in close],
                    "close": close,
                    "volume": [1_000 + index * 10 for index in range(periods)],
                }
            )
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    pd.concat(frames, ignore_index=True).to_csv(path, index=False)
    return path


@pytest.fixture
def snapshot_env(monkeypatch, tmp_path):
    """Point the API at a temporary snapshot and return a writer for it."""

    def _use(**kwargs) -> Path:
        path = _write_snapshot(tmp_path / "snapshot.csv", **kwargs)
        monkeypatch.setenv("SNAPSHOT_PATH", str(path))
        return path

    monkeypatch.delenv("DATA_DIR", raising=False)
    monkeypatch.delenv("SNAPSHOT_STALE_DAYS", raising=False)
    _cached_forecasts.cache_clear()
    return _use


def test_health_endpoint():
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_home_page_and_forecasts_endpoint():
    home = client.get("/")
    assert home.status_code == 200
    assert "Dự báo thị trường Việt Nam" in home.text

    response = client.get("/forecasts")
    assert response.status_code == 200
    assert response.json()["forecasts"][0]["model_version"] == "baseline-logistic-v1"


def test_recommendation_requires_confirmation():
    response = client.post(
        "/recommendations",
        json={"confirmed": False, "risk_profile": "balanced", "symbols": ["AAA"]},
    )
    assert response.status_code == 400


def test_snapshot_path_is_read_from_environment(snapshot_env):
    snapshot_env(symbols=("XXX", "YYY"))

    response = client.get("/forecasts")

    assert response.status_code == 200
    symbols = {item["symbol"] for item in response.json()["forecasts"]}
    assert symbols == {"XXX", "YYY"}


def test_forecasts_do_not_depend_on_the_working_directory(monkeypatch, tmp_path):
    monkeypatch.delenv("SNAPSHOT_PATH", raising=False)
    monkeypatch.delenv("DATA_DIR", raising=False)
    monkeypatch.chdir(tmp_path)

    response = client.get("/forecasts")

    assert response.status_code == 200
    assert response.json()["forecasts"]


def test_missing_snapshot_returns_503(monkeypatch, tmp_path):
    monkeypatch.setenv("SNAPSHOT_PATH", str(tmp_path / "khong-ton-tai.csv"))

    response = client.get("/forecasts")

    assert response.status_code == 503
    assert "snapshot" in response.json()["detail"].lower()


def test_invalid_snapshot_returns_503(monkeypatch, tmp_path):
    broken = tmp_path / "broken.csv"
    broken.write_text(
        "date,symbol,open,high,low,close,volume\n2024-01-02,AAA,10,8,9,10,100\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("SNAPSHOT_PATH", str(broken))

    response = client.get("/forecasts")

    assert response.status_code == 503


def test_invalid_snapshot_metadata_returns_503(snapshot_env, tmp_path):
    snapshot_env()
    (tmp_path / "snapshot.metadata.json").write_text("{not-json", encoding="utf-8")

    response = client.get("/forecasts")

    assert response.status_code == 503
    assert "snapshot" in response.json()["detail"].lower()


def test_forecast_as_of_is_the_last_session_in_the_snapshot(snapshot_env):
    path = snapshot_env(periods=60)
    last_session = pd.read_csv(path, parse_dates=["date"])["date"].max().date().isoformat()

    payload = client.get("/forecasts").json()

    assert payload["forecasts"]
    assert {item["as_of"] for item in payload["forecasts"]} == {last_session}


def test_every_symbol_gets_exactly_one_forecast(snapshot_env):
    snapshot_env(symbols=("AAA", "BBB", "CCC"), periods=60)

    forecasts = client.get("/forecasts").json()["forecasts"]

    assert [item["symbol"] for item in forecasts].count("AAA") == 1
    assert len(forecasts) == 3


def test_snapshot_without_labelled_rows_returns_422(snapshot_env):
    # 25 sessions: enough for the 20-session feature warmup, but no row has both
    # complete features and a 20-session forward return, so training is impossible.
    snapshot_env(periods=25)

    response = client.get("/forecasts")

    assert response.status_code == 422
    assert "lịch sử" in response.json()["detail"]


def test_online_mode_uses_the_same_fallback_safe_pipeline(snapshot_env):
    snapshot_env()

    response = client.get("/forecasts", params={"mode": "online"})

    assert response.status_code == 200
    assert response.json()["mode"] == "online"


def test_forecasts_are_ranked_by_probability(snapshot_env):
    snapshot_env(symbols=("AAA", "BBB", "CCC", "DDD"))

    forecasts = client.get("/forecasts").json()["forecasts"]

    probabilities = [item["positive_return_probability"] for item in forecasts]
    assert probabilities == sorted(probabilities, reverse=True)
    assert len(set(probabilities)) > 1, "fixture should not degenerate to one class"


def test_response_carries_snapshot_provenance(snapshot_env):
    path = snapshot_env()
    frame = pd.read_csv(path, parse_dates=["date"])

    payload = client.get("/forecasts").json()

    assert payload["source"] == str(path)
    assert payload["data_start"] == frame["date"].min().date().isoformat()
    assert payload["data_end"] == frame["date"].max().date().isoformat()
    assert payload["snapshot_retrieved_at"]


def test_provenance_is_read_from_the_metadata_sidecar(snapshot_env, tmp_path):
    path = snapshot_env()
    (tmp_path / "snapshot.metadata.json").write_text(
        json.dumps(
            {
                "source": "vnstock:VCI",
                "retrieved_at": "2026-10-15T06:50:12+00:00",
                "start_date": "2024-01-01",
                "end_date": "2024-03-20",
            }
        ),
        encoding="utf-8",
    )

    payload = client.get("/forecasts").json()

    assert payload["source"] == "vnstock:VCI"
    assert payload["data_end"] == "2024-03-20"


def test_old_snapshot_warns_without_failing(snapshot_env):
    snapshot_env()  # fixture data ends in 2024

    payload = client.get("/forecasts").json()

    assert payload["forecasts"], "a stale snapshot must still return forecasts"
    assert any("dữ liệu nghiên cứu offline" in text for text in payload["warnings"])


def test_future_dated_rows_are_rejected_as_unreadable(snapshot_env):
    # OHLCV for a session that has not happened yet means a broken source or a wrong
    # host clock. validate_market_frame rejects it, so the API must answer 503 in
    # Vietnamese rather than leak the traceback.
    snapshot_env(end=(date.today() + timedelta(days=60)).isoformat())

    response = client.get("/forecasts")

    assert response.status_code == 503
    assert "snapshot" in response.json()["detail"].lower()


def test_sidecar_claiming_a_future_end_date_is_reported(snapshot_env, tmp_path):
    # The CSV itself is valid, but `end_date` is read from the sidecar as written, so a
    # sidecar that disagrees with its CSV must still surface instead of passing silently.
    snapshot_env()
    future = (date.today() + timedelta(days=60)).isoformat()
    (tmp_path / "snapshot.metadata.json").write_text(
        json.dumps({"source": "vnstock:VCI", "end_date": future}),
        encoding="utf-8",
    )

    payload = client.get("/forecasts").json()

    assert payload["forecasts"]
    assert any("tương lai" in text for text in payload["warnings"])


def test_long_unknown_symbol_lists_are_truncated(snapshot_env):
    snapshot_env(symbols=("AAA", "BBB"))

    def ask(unknown_count: int) -> str:
        return client.post(
            "/recommendations",
            json={
                "confirmed": True,
                "risk_profile": "balanced",
                "symbols": ["AAA"] + [f"X{index:04d}" for index in range(unknown_count)],
            },
        ).json()["message"]

    short, long = ask(20), ask(500)

    assert "Bỏ qua 500 mã" in long
    assert "và 490 mã khác" in long
    # The request size is not bounded by the contract, so the message must not grow with
    # it: only the counted digits differ between 20 and 500 skipped symbols.
    assert abs(len(long) - len(short)) <= 10


def test_fresh_snapshot_has_no_age_warning(snapshot_env):
    snapshot_env(end=date.today().isoformat())

    payload = client.get("/forecasts").json()

    assert not any("cũ" in text for text in payload["warnings"])


def test_stale_threshold_is_configurable(snapshot_env, monkeypatch):
    # The API measures age on the Vietnamese date while the fixture is built from the
    # host date; on a UTC runner those differ by a day after 17:00 UTC. Ending 3 host
    # days back gives an age of 3 or 4, so thresholds of 2 and 10 hold at any hour.
    snapshot_env(end=(date.today() - timedelta(days=3)).isoformat())

    monkeypatch.setenv("SNAPSHOT_STALE_DAYS", "2")
    _cached_forecasts.cache_clear()
    assert any("cũ" in text for text in client.get("/forecasts").json()["warnings"])

    monkeypatch.setenv("SNAPSHOT_STALE_DAYS", "10")
    assert not client.get("/forecasts").json()["warnings"]


def test_single_class_snapshot_warns_that_the_model_cannot_separate(snapshot_env):
    snapshot_env(pattern="trend")

    payload = client.get("/forecasts").json()

    probabilities = {item["positive_return_probability"] for item in payload["forecasts"]}
    assert len(probabilities) == 1, "monotone prices should collapse to one class"
    assert any("một lớp kết quả" in text for text in payload["warnings"])


def test_single_symbol_snapshot_with_both_classes_is_not_warned(snapshot_env):
    # One symbol trivially yields one probability. That alone is not degeneracy, so the
    # warning must look at the training labels instead of at the predictions.
    snapshot_env(symbols=("FPT",), periods=100, pattern="cycle")

    payload = client.get("/forecasts").json()

    assert len(payload["forecasts"]) == 1
    assert not any("một lớp kết quả" in text for text in payload["warnings"])


def test_single_symbol_snapshot_with_one_class_is_warned(snapshot_env):
    # The case that matters most: a monotone one-symbol snapshot predicts a flat 100%.
    snapshot_env(symbols=("FPT",), periods=100, pattern="trend")

    payload = client.get("/forecasts").json()

    assert payload["forecasts"][0]["positive_return_probability"] == 1.0
    assert any("một lớp kết quả" in text for text in payload["warnings"])
    assert any("mọi phiên đều tăng" in text for text in payload["warnings"])


def test_shortlist_flags_symbols_below_an_even_chance(snapshot_env):
    snapshot_env(symbols=("AAA", "BBB", "CCC", "DDD"))
    symbols = [item["symbol"] for item in client.get("/forecasts").json()["forecasts"]]

    payload = client.post(
        "/recommendations",
        json={"confirmed": True, "risk_profile": "conservative", "symbols": symbols},
    ).json()

    shortlisted = client.get("/forecasts").json()["forecasts"]
    probabilities = {item["symbol"]: item["positive_return_probability"] for item in shortlisted}
    if any(probabilities[symbol] < 0.5 for symbol in payload["symbols"]):
        assert "dưới 50%" in payload["message"]
    else:
        assert "dưới 50%" not in payload["message"]


def test_evidence_is_per_symbol_and_in_vietnamese(snapshot_env):
    snapshot_env(symbols=("AAA", "BBB"))

    forecasts = client.get("/forecasts").json()["forecasts"]

    for item in forecasts:
        assert len(item["evidence"]) == 5
        assert any("Động lượng 20 phiên" in line for line in item["evidence"])
    assert forecasts[0]["evidence"] != forecasts[1]["evidence"]


def test_dashboard_labels_confidence_without_promising_profit():
    page = client.get("/").text

    assert "Độ tin cậy của hệ thống" in page
    assert "không phải xác suất sinh lời" in page
    assert "Bằng chứng" in page


def test_forecast_disclaimer_is_vietnamese_and_promises_no_profit(snapshot_env):
    # The dashboard renders `disclaimer` verbatim next to the table, so an English
    # string here would be the one English sentence on a Vietnamese page.
    snapshot_env()

    disclaimer = client.get("/forecasts").json()["disclaimer"]

    assert "không phải tư vấn đầu tư" in disclaimer
    assert "không cam kết lợi nhuận" in disclaimer
    assert "advice" not in disclaimer.lower()


def _forecast(symbol: str, *, probability: float, expected_return: float, volatility: float):
    return Forecast(
        symbol=symbol,
        as_of=date(2024, 6, 28),
        horizon_days=20,
        positive_return_probability=probability,
        expected_return=expected_return,
        expected_volatility=volatility,
        confidence=abs(probability - 0.5) * 2,
        evidence=[],
        model_version="test",
        data_timestamp=datetime(2024, 6, 28, tzinfo=timezone.utc),
    )


def test_risk_profiles_rank_the_same_candidates_differently():
    steady = _forecast("STEADY", probability=0.60, expected_return=0.02, volatility=0.01)
    risky = _forecast("RISKY", probability=0.52, expected_return=0.20, volatility=0.09)

    conservative = _rank_for_profile([steady, risky], RiskProfile.conservative)
    growth = _rank_for_profile([steady, risky], RiskProfile.growth)

    assert conservative[0].symbol == "STEADY"
    assert growth[0].symbol == "RISKY"


def test_ranking_caps_the_shortlist_per_profile():
    candidates = [
        _forecast(f"S{index}", probability=0.5 + index / 100, expected_return=0.01 * index, volatility=0.01)
        for index in range(8)
    ]

    assert len(_rank_for_profile(candidates, RiskProfile.conservative)) == 3
    assert len(_rank_for_profile(candidates, RiskProfile.balanced)) == 5
    assert len(_rank_for_profile(candidates, RiskProfile.growth)) == 5


def test_ranking_is_stable_when_every_metric_ties():
    tied = [
        _forecast(symbol, probability=0.5, expected_return=0.01, volatility=0.02)
        for symbol in ("CCC", "AAA", "BBB")
    ]

    ranked = _rank_for_profile(tied, RiskProfile.balanced)

    assert [item.symbol for item in ranked] == ["AAA", "BBB", "CCC"]


def test_recommendation_returns_a_ranked_subset_of_the_request(snapshot_env):
    snapshot_env(symbols=("AAA", "BBB", "CCC", "DDD"))

    payload = client.post(
        "/recommendations",
        json={"confirmed": True, "risk_profile": "conservative", "symbols": ["AAA", "BBB", "CCC", "DDD"]},
    ).json()

    assert payload["confirmed"] is True
    assert payload["risk_profile"] == "conservative"
    assert set(payload["symbols"]) <= {"AAA", "BBB", "CCC", "DDD"}
    assert len(payload["symbols"]) == 3, "conservative caps the shortlist at three symbols"


def test_recommendation_accepts_lowercase_symbols(snapshot_env):
    snapshot_env(symbols=("AAA", "BBB"))

    payload = client.post(
        "/recommendations",
        json={"confirmed": True, "risk_profile": "balanced", "symbols": ["aaa", " bbb "]},
    ).json()

    assert set(payload["symbols"]) == {"AAA", "BBB"}


def test_recommendation_skips_unknown_symbols_and_says_so(snapshot_env):
    snapshot_env(symbols=("AAA", "BBB"))

    payload = client.post(
        "/recommendations",
        json={"confirmed": True, "risk_profile": "balanced", "symbols": ["AAA", "KHONGCO"]},
    ).json()

    assert "KHONGCO" not in payload["symbols"]
    assert "KHONGCO" in payload["message"]
    assert "Bỏ qua" in payload["message"]


def test_recommendation_with_no_known_symbol_returns_422(snapshot_env):
    snapshot_env(symbols=("AAA",))

    response = client.post(
        "/recommendations",
        json={"confirmed": True, "risk_profile": "balanced", "symbols": ["KHONGCO"]},
    )

    assert response.status_code == 422
    assert "KHONGCO" in response.json()["detail"]


def test_recommendation_message_carries_provenance_and_no_profit_promise(snapshot_env):
    snapshot_env(symbols=("AAA", "BBB"))

    message = client.post(
        "/recommendations",
        json={"confirmed": True, "risk_profile": "growth", "symbols": ["AAA", "BBB"]},
    ).json()["message"]

    assert "28/06/2024" in message or "snapshot" in message
    assert "không cam kết lợi nhuận" in message
    assert "dữ liệu nghiên cứu offline" in message, "stale-data warning must reach this endpoint"


def test_recommendation_repeats_the_degenerate_model_warning(snapshot_env):
    snapshot_env(symbols=("AAA", "BBB"), pattern="trend")

    message = client.post(
        "/recommendations",
        json={"confirmed": True, "risk_profile": "balanced", "symbols": ["AAA", "BBB"]},
    ).json()["message"]

    assert "một lớp kết quả" in message


def test_dashboard_gates_recommendations_behind_confirmation():
    page = client.get("/").text

    assert 'id="confirm"' in page
    assert 'id="profile"' in page
    assert 'id="advise" disabled' in page


def test_repeated_requests_reuse_the_trained_model(snapshot_env):
    snapshot_env()

    client.get("/forecasts")
    hits_before = _cached_forecasts.cache_info().hits
    client.get("/forecasts")
    client.post(
        "/recommendations",
        json={"confirmed": True, "risk_profile": "balanced", "symbols": ["AAA"]},
    )

    assert _cached_forecasts.cache_info().hits == hits_before + 2


def test_cache_is_invalidated_when_the_snapshot_file_changes(snapshot_env, tmp_path):
    path = snapshot_env(symbols=("AAA", "BBB"))
    assert {item["symbol"] for item in client.get("/forecasts").json()["forecasts"]} == {
        "AAA",
        "BBB",
    }

    # Same path and byte length, so only mtime can signal the change. Bump it explicitly
    # instead of trusting the filesystem clock granularity.
    _write_snapshot(path, symbols=("XXX", "YYY"))
    stat = path.stat()
    os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1_000_000_000))

    assert {item["symbol"] for item in client.get("/forecasts").json()["forecasts"]} == {
        "XXX",
        "YYY",
    }


def test_warnings_are_recomputed_outside_the_cache(snapshot_env, monkeypatch):
    end = date.today() - timedelta(days=5)
    snapshot_env(end=end.isoformat())
    assert client.get("/forecasts").json()["warnings"] == [], "5 days is inside the 7-day default"

    # The snapshot file is untouched, so a warning now can only come from outside the
    # cached result — which is what keeps the reported data age fresh on a long-running app.
    monkeypatch.setenv("SNAPSHOT_STALE_DAYS", "0")

    # Age is counted on the Vietnamese date, which runs a day ahead of a UTC host after
    # 17:00 UTC, so derive the expected figure the same way instead of hard-coding 5.
    age = (datetime.now(VN_TIMEZONE).date() - end).days
    assert any(f"cũ {age} ngày" in text for text in client.get("/forecasts").json()["warnings"])
