from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd
from sklearn.dummy import DummyClassifier
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler
from sklearn.linear_model import LogisticRegression
from sklearn.linear_model import Ridge

from .features import FEATURE_COLUMNS


@dataclass
class BaselineModel:
    estimator: object
    return_estimator: object | None = None
    version: str = "baseline-logistic-v1"

    def fit(self, frame: pd.DataFrame) -> "BaselineModel":
        clean = frame.dropna(subset=FEATURE_COLUMNS + ["target_positive", "forward_return"])
        if clean.empty:
            raise ValueError("No complete rows available to train the model")
        x = clean[FEATURE_COLUMNS]
        y = clean["target_positive"].astype(int)
        if y.nunique() < 2:
            self.estimator = DummyClassifier(strategy="prior")
        else:
            self.estimator = make_pipeline(
                StandardScaler(),
                LogisticRegression(max_iter=1000, class_weight="balanced"),
            )
        self.estimator.fit(x, y)
        self.return_estimator = make_pipeline(StandardScaler(), Ridge(alpha=1.0))
        self.return_estimator.fit(x, clean["forward_return"].astype(float))
        return self

    def predict_probability(self, frame: pd.DataFrame) -> np.ndarray:
        clean = frame[FEATURE_COLUMNS].copy()
        if clean.isna().any().any():
            raise ValueError("Cannot forecast rows with missing feature values")
        probabilities = self.estimator.predict_proba(clean)
        classes = getattr(self.estimator, "classes_", np.array([0, 1]))
        if len(classes) == 1:
            return np.full(len(clean), float(classes[0] == 1))
        positive_index = int(np.where(classes == 1)[0][0])
        return probabilities[:, positive_index]

    def predict_expected_return(self, frame: pd.DataFrame) -> np.ndarray:
        clean = frame[FEATURE_COLUMNS].copy()
        if clean.isna().any().any():
            raise ValueError("Cannot forecast rows with missing feature values")
        if self.return_estimator is None:
            raise RuntimeError("Model has not been fitted")
        return self.return_estimator.predict(clean)
