"""Baseline model for Vietnamese equity medium-term return forecasting.

Implements a leakage-safe binary classifier (positive 20-day return) and a
complementary ridge regression return estimator.  Both estimators are
wrapped in an sklearn Pipeline so callers never see raw arrays.

Lane: Modeling (Issue #5).
"""

from __future__ import annotations

import json
import pickle
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from sklearn.dummy import DummyClassifier
from sklearn.linear_model import LogisticRegression, Ridge
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

from .features import FEATURE_COLUMNS


# ---------------------------------------------------------------------------
# Risk-profile shortlist criteria (used by the API for /recommendations).
# Lane Modeling owns these weights; lane App only reads them.
# ---------------------------------------------------------------------------

RISK_PROFILE_WEIGHTS: dict[str, dict[str, float]] = {
    "conservative": {
        "probability": 0.50,
        "expected_return": 0.10,
        "low_volatility": 0.40,
        "top_n": 3,
    },
    "balanced": {
        "probability": 0.40,
        "expected_return": 0.30,
        "low_volatility": 0.30,
        "top_n": 5,
    },
    "growth": {
        "probability": 0.30,
        "expected_return": 0.60,
        "low_volatility": 0.10,
        "top_n": 5,
    },
}


# ---------------------------------------------------------------------------
# BaselineModel
# ---------------------------------------------------------------------------


@dataclass
class BaselineModel:
    """Two-head model: binary direction classifier + ridge return regressor.

    The classifier predicts the probability that the 20-day forward return is
    positive; the regressor estimates the expected magnitude.  When only one
    class is present in the training set (e.g. tiny or pathological data) the
    classifier falls back to a ``DummyClassifier`` and the API emits a warning
    via ``is_dummy``.
    """

    estimator: object
    return_estimator: object | None = None
    version: str = "baseline-logistic-v1"
    is_dummy: bool = field(default=False, compare=False, repr=False)

    # ------------------------------------------------------------------
    # Fitting
    # ------------------------------------------------------------------

    def fit(self, frame: pd.DataFrame) -> "BaselineModel":
        """Fit both estimators on *frame*, ignoring rows with NaN in features or targets.

        Args:
            frame: DataFrame produced by ``build_features``.  Must contain
                   ``FEATURE_COLUMNS``, ``target_positive``, and
                   ``forward_return``.

        Returns:
            self (for chaining).

        Raises:
            ValueError: If no complete rows are available after dropping NaNs.
        """
        clean = frame.dropna(subset=FEATURE_COLUMNS + ["target_positive", "forward_return"])
        if clean.empty:
            raise ValueError("No complete rows available to train the model")

        x = clean[FEATURE_COLUMNS]
        y = clean["target_positive"].astype(int)

        if y.nunique() < 2:
            self.estimator = DummyClassifier(strategy="prior")
            self.is_dummy = True
        else:
            self.estimator = make_pipeline(
                StandardScaler(),
                LogisticRegression(max_iter=1000, class_weight="balanced"),
            )
            self.is_dummy = False

        self.estimator.fit(x, y)

        self.return_estimator = make_pipeline(StandardScaler(), Ridge(alpha=1.0))
        self.return_estimator.fit(x, clean["forward_return"].astype(float))
        return self

    # ------------------------------------------------------------------
    # Inference
    # ------------------------------------------------------------------

    def predict_probability(self, frame: pd.DataFrame) -> np.ndarray:
        """Return P(forward_return > 0) for each row in *frame*.

        Args:
            frame: DataFrame with at least ``FEATURE_COLUMNS`` filled (no NaN).

        Returns:
            1-D float array of probabilities in [0, 1].

        Raises:
            ValueError: If any feature value is NaN.
        """
        x = frame[FEATURE_COLUMNS].copy()
        if x.isna().any().any():
            raise ValueError("Cannot forecast rows with missing feature values")
        probabilities = self.estimator.predict_proba(x)
        classes = getattr(self.estimator, "classes_", np.array([0, 1]))
        if len(classes) == 1:
            return np.full(len(x), float(classes[0] == 1))
        positive_index = int(np.where(classes == 1)[0][0])
        return probabilities[:, positive_index]

    def predict_expected_return(self, frame: pd.DataFrame) -> np.ndarray:
        """Return the estimated forward return (signed float) for each row.

        Args:
            frame: DataFrame with at least ``FEATURE_COLUMNS`` filled (no NaN).

        Returns:
            1-D float array of expected returns (e.g. 0.05 = 5 %).

        Raises:
            ValueError: If any feature value is NaN.
            RuntimeError: If the model has not been fitted.
        """
        x = frame[FEATURE_COLUMNS].copy()
        if x.isna().any().any():
            raise ValueError("Cannot forecast rows with missing feature values")
        if self.return_estimator is None:
            raise RuntimeError("Model has not been fitted")
        return self.return_estimator.predict(x)

    def predict_confidence(self, frame: pd.DataFrame) -> np.ndarray:
        """Return a system-confidence score in [0, 1] for each row.

        Confidence is ``abs(p - 0.5) * 2``, i.e. 0 when the model is
        maximally uncertain and 1 when it is at the decision boundary
        extremes.  This is a provisional formula for the MVP and is owned by
        lane Modeling (CONTRACTS_VI §3.1).

        Args:
            frame: Same requirements as ``predict_probability``.

        Returns:
            1-D float array in [0, 1].
        """
        probs = self.predict_probability(frame)
        return np.abs(probs - 0.5) * 2.0

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def save(self, path: str | Path) -> Path:
        """Persist the model to *path* (pickle) and write a metadata sidecar.

        Args:
            path: Target ``.pkl`` file path.  Parent directories are created
                  automatically.  A ``<name>.metadata.json`` sidecar is
                  written next to the pickle.

        Returns:
            Resolved ``Path`` of the written pickle.
        """
        pkl_path = Path(path)
        pkl_path.parent.mkdir(parents=True, exist_ok=True)
        with pkl_path.open("wb") as f:
            pickle.dump(self, f, protocol=pickle.HIGHEST_PROTOCOL)

        meta: dict[str, Any] = {
            "version": self.version,
            "is_dummy": self.is_dummy,
            "feature_columns": FEATURE_COLUMNS,
        }
        meta_path = pkl_path.with_suffix(".metadata.json")
        with meta_path.open("w", encoding="utf-8") as mf:
            json.dump(meta, mf, indent=2, ensure_ascii=False)
            mf.write("\n")

        return pkl_path

    @classmethod
    def load(cls, path: str | Path) -> "BaselineModel":
        """Load a previously saved model from *path*.

        Args:
            path: Path to the ``.pkl`` file written by :meth:`save`.

        Returns:
            The deserialized ``BaselineModel`` instance.

        Raises:
            FileNotFoundError: If the pickle does not exist.
            ValueError: If the unpickled object is not a ``BaselineModel``.
        """
        pkl_path = Path(path)
        if not pkl_path.is_file():
            raise FileNotFoundError(f"Model artifact not found: {pkl_path}")
        with pkl_path.open("rb") as f:
            obj = pickle.load(f)
        if not isinstance(obj, cls):
            raise ValueError(
                f"Expected a {cls.__name__} but got {type(obj).__name__}: {pkl_path}"
            )
        return obj
