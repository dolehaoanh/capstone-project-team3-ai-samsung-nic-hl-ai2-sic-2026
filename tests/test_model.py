"""Tests for model.py — BaselineModel and RISK_PROFILE_WEIGHTS.

All tests run fully offline using synthetic fixtures.
"""

from __future__ import annotations

import pickle
import tempfile
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from vietnam_stock_forecasting.features import FEATURE_COLUMNS, build_features
from vietnam_stock_forecasting.model import BaselineModel, RISK_PROFILE_WEIGHTS


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_market(n: int = 120, symbols: list[str] | None = None) -> pd.DataFrame:
    """Synthetic market DataFrame with two symbols and oscillating prices.

    The price series oscillates so that ``build_features`` produces both
    positive and negative 20-day forward returns, giving both classes in
    ``target_positive`` and allowing ``BaselineModel`` to use
    ``LogisticRegression`` instead of ``DummyClassifier``.
    """
    import math

    syms = symbols or ["AAA", "BBB"]
    frames = []
    for i, sym in enumerate(syms):
        base = 100 + i * 10
        dates = pd.date_range("2024-01-01", periods=n)
        # Oscillate with a clear sine wave so forward returns flip sign
        amplitude = 15
        period = 40  # one full cycle every 40 days
        close = [
            base + amplitude * math.sin(2 * math.pi * j / period)
            for j in range(n)
        ]
        frames.append(
            pd.DataFrame(
                {
                    "date": dates,
                    "symbol": sym,
                    "open": [c * 0.99 for c in close],
                    "high": [c * 1.01 for c in close],
                    "low": [c * 0.98 for c in close],
                    "close": close,
                    "volume": [1_000 + j * 10 for j in range(n)],
                }
            )
        )
    return pd.concat(frames, ignore_index=True)


@pytest.fixture()
def featured_frame() -> pd.DataFrame:
    market = _make_market(n=120)
    return build_features(market, horizon_days=20)


@pytest.fixture()
def clean_frame(featured_frame: pd.DataFrame) -> pd.DataFrame:
    return featured_frame.dropna(
        subset=FEATURE_COLUMNS + ["target_positive", "forward_return"]
    )


# ---------------------------------------------------------------------------
# BaselineModel.fit
# ---------------------------------------------------------------------------


class TestBaselineModelFit:
    def test_fit_returns_self(self, clean_frame: pd.DataFrame) -> None:
        model = BaselineModel(estimator=None)
        result = model.fit(clean_frame)
        assert result is model

    def test_fit_sets_both_estimators(self, clean_frame: pd.DataFrame) -> None:
        model = BaselineModel(estimator=None).fit(clean_frame)
        assert model.estimator is not None
        assert model.return_estimator is not None

    def test_fit_raises_on_empty_frame(self) -> None:
        empty = pd.DataFrame(columns=FEATURE_COLUMNS + ["target_positive", "forward_return"])
        with pytest.raises(ValueError, match="No complete rows"):
            BaselineModel(estimator=None).fit(empty)

    def test_fit_uses_dummy_when_single_class(self) -> None:
        """When all labels are the same, fall back to DummyClassifier."""
        market = _make_market(n=60, symbols=["AAA"])
        # Force all forward returns to be negative → target_positive always 0
        featured = build_features(market, horizon_days=5)
        featured = featured.dropna(subset=FEATURE_COLUMNS + ["target_positive", "forward_return"])
        featured = featured.copy()
        featured["forward_return"] = -0.01
        featured["target_positive"] = 0
        model = BaselineModel(estimator=None).fit(featured)
        assert model.is_dummy is True

    def test_fit_is_not_dummy_with_two_symbols_varying_returns(
        self, clean_frame: pd.DataFrame
    ) -> None:
        model = BaselineModel(estimator=None).fit(clean_frame)
        # Two symbols with opposing trends → both classes should appear
        assert model.is_dummy is False

    def test_fit_does_not_use_future_feature_columns(
        self, featured_frame: pd.DataFrame
    ) -> None:
        """Ensure that forward_return and target_positive are NOT in FEATURE_COLUMNS."""
        assert "forward_return" not in FEATURE_COLUMNS
        assert "target_positive" not in FEATURE_COLUMNS


# ---------------------------------------------------------------------------
# BaselineModel.predict_probability
# ---------------------------------------------------------------------------


class TestPredictProbability:
    def test_probabilities_are_in_unit_interval(self, clean_frame: pd.DataFrame) -> None:
        model = BaselineModel(estimator=None).fit(clean_frame)
        probs = model.predict_probability(clean_frame)
        assert probs.shape == (len(clean_frame),)
        assert np.all(probs >= 0.0)
        assert np.all(probs <= 1.0)

    def test_raises_on_nan_features(self, clean_frame: pd.DataFrame) -> None:
        model = BaselineModel(estimator=None).fit(clean_frame)
        dirty = clean_frame.copy()
        dirty.iloc[0, dirty.columns.get_loc(FEATURE_COLUMNS[0])] = float("nan")
        with pytest.raises(ValueError, match="missing feature"):
            model.predict_probability(dirty)

    def test_output_length_matches_input(self, clean_frame: pd.DataFrame) -> None:
        model = BaselineModel(estimator=None).fit(clean_frame)
        subset = clean_frame.head(10)
        probs = model.predict_probability(subset)
        assert len(probs) == 10


# ---------------------------------------------------------------------------
# BaselineModel.predict_expected_return
# ---------------------------------------------------------------------------


class TestPredictExpectedReturn:
    def test_returns_finite_floats(self, clean_frame: pd.DataFrame) -> None:
        model = BaselineModel(estimator=None).fit(clean_frame)
        rets = model.predict_expected_return(clean_frame)
        assert rets.shape == (len(clean_frame),)
        assert np.all(np.isfinite(rets))

    def test_raises_on_nan_features(self, clean_frame: pd.DataFrame) -> None:
        model = BaselineModel(estimator=None).fit(clean_frame)
        dirty = clean_frame.copy()
        dirty.iloc[0, dirty.columns.get_loc(FEATURE_COLUMNS[1])] = float("nan")
        with pytest.raises(ValueError, match="missing feature"):
            model.predict_expected_return(dirty)

    def test_raises_before_fit(self) -> None:
        model = BaselineModel(estimator=None)  # not fitted
        # return_estimator is None → RuntimeError
        with pytest.raises((RuntimeError, AttributeError)):
            model.predict_expected_return(
                pd.DataFrame({c: [0.0] for c in FEATURE_COLUMNS})
            )


# ---------------------------------------------------------------------------
# BaselineModel.predict_confidence
# ---------------------------------------------------------------------------


class TestPredictConfidence:
    def test_confidence_in_unit_interval(self, clean_frame: pd.DataFrame) -> None:
        model = BaselineModel(estimator=None).fit(clean_frame)
        conf = model.predict_confidence(clean_frame)
        assert np.all(conf >= 0.0)
        assert np.all(conf <= 1.0)

    def test_confidence_is_zero_at_half_probability(
        self, clean_frame: pd.DataFrame
    ) -> None:
        model = BaselineModel(estimator=None).fit(clean_frame)
        probs = model.predict_probability(clean_frame)
        conf = model.predict_confidence(clean_frame)
        expected = np.abs(probs - 0.5) * 2.0
        np.testing.assert_array_almost_equal(conf, expected)


# ---------------------------------------------------------------------------
# BaselineModel.save / load
# ---------------------------------------------------------------------------


class TestSaveLoad:
    def test_roundtrip_preserves_predictions(self, clean_frame: pd.DataFrame) -> None:
        model = BaselineModel(estimator=None).fit(clean_frame)
        probs_before = model.predict_probability(clean_frame)

        with tempfile.TemporaryDirectory() as tmpdir:
            pkl_path = Path(tmpdir) / "model.pkl"
            model.save(pkl_path)
            assert pkl_path.is_file()
            meta_path = pkl_path.with_suffix(".metadata.json")
            assert meta_path.is_file()

            loaded = BaselineModel.load(pkl_path)

        probs_after = loaded.predict_probability(clean_frame)
        np.testing.assert_array_almost_equal(probs_before, probs_after)

    def test_load_raises_if_file_missing(self) -> None:
        with pytest.raises(FileNotFoundError):
            BaselineModel.load("nonexistent_path/model.pkl")

    def test_load_raises_if_wrong_type(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            bad_pkl = Path(tmpdir) / "bad.pkl"
            with bad_pkl.open("wb") as f:
                pickle.dump({"not": "a model"}, f)
            with pytest.raises(ValueError, match="Expected a BaselineModel"):
                BaselineModel.load(bad_pkl)

    def test_metadata_sidecar_has_version(self, clean_frame: pd.DataFrame) -> None:
        import json

        model = BaselineModel(estimator=None).fit(clean_frame)
        with tempfile.TemporaryDirectory() as tmpdir:
            pkl_path = Path(tmpdir) / "model.pkl"
            model.save(pkl_path)
            meta = json.loads(pkl_path.with_suffix(".metadata.json").read_text())
        assert "version" in meta
        assert meta["version"] == model.version


# ---------------------------------------------------------------------------
# RISK_PROFILE_WEIGHTS contract
# ---------------------------------------------------------------------------


class TestRiskProfileWeights:
    def test_all_profiles_present(self) -> None:
        assert set(RISK_PROFILE_WEIGHTS.keys()) == {"conservative", "balanced", "growth"}

    def test_weights_sum_to_one_per_profile(self) -> None:
        for profile, weights in RISK_PROFILE_WEIGHTS.items():
            numeric_weights = {k: v for k, v in weights.items() if k != "top_n"}
            total = sum(numeric_weights.values())
            assert abs(total - 1.0) < 1e-9, (
                f"Weights for '{profile}' sum to {total}, expected 1.0"
            )

    def test_top_n_is_positive_int(self) -> None:
        for profile, weights in RISK_PROFILE_WEIGHTS.items():
            assert isinstance(weights["top_n"], (int, float))
            assert weights["top_n"] > 0

    def test_all_weights_non_negative(self) -> None:
        for profile, weights in RISK_PROFILE_WEIGHTS.items():
            for key, val in weights.items():
                if key != "top_n":
                    assert val >= 0, f"Negative weight in '{profile}.{key}'"
