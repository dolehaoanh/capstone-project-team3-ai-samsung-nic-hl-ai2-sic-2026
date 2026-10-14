from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse

from .contracts import (
    DataMode,
    Forecast,
    ForecastResponse,
    RecommendationRequest,
    RecommendationResponse,
    RiskProfile,
)
from .data import load_csv_snapshot
from .features import FEATURE_COLUMNS, build_features
from .model import BaselineModel

app = FastAPI(title="Vietnam Stock Forecasting MVP", version="0.1.0")
DISCLAIMER = (
    "This is educational decision-support software, not personalized financial advice. "
    "Forecasts are uncertain and do not guarantee returns."
)


def _default_snapshot() -> Path:
    return Path("data/sample/vn30_sample.csv")


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "service": "vietnam-stock-forecasting"}


@app.get("/", response_class=HTMLResponse)
def home() -> str:
    return """<!doctype html>
<html lang="vi">
  <head><meta charset="utf-8"><title>Vietnam Stock Forecasting</title>
    <style>body{font-family:system-ui;max-width:960px;margin:2rem auto;padding:0 1rem}
    table{border-collapse:collapse;width:100%}td,th{border:1px solid #ddd;padding:.5rem}
    .muted{color:#666}</style>
  </head>
  <body><h1>Dự báo thị trường Việt Nam</h1>
    <p class="muted">Công cụ nghiên cứu giáo dục; không đảm bảo lợi nhuận hay thay thế tư vấn tài chính.</p>
    <button id="load">Tải dự báo snapshot</button><p id="status"></p>
    <table><thead><tr><th>Mã</th><th>Xác suất tăng</th><th>Lợi nhuận kỳ vọng</th><th>Độ biến động</th><th>Độ tin cậy</th></tr></thead>
    <tbody id="rows"></tbody></table>
    <script>
      document.querySelector("#load").onclick = async () => {
        const status = document.querySelector("#status");
        status.textContent = "Đang tải...";
        const response = await fetch("/forecasts");
        const data = await response.json();
        if (!response.ok) { status.textContent = data.detail || "Không tải được dữ liệu"; return; }
        status.textContent = `Thời điểm: ${data.generated_at} | Nguồn: ${data.mode}`;
        document.querySelector("#rows").innerHTML = data.forecasts.map(item =>
          `<tr><td>${item.symbol}</td><td>${(item.positive_return_probability*100).toFixed(1)}%</td>
          <td>${(item.expected_return*100).toFixed(2)}%</td><td>${(item.expected_volatility*100).toFixed(2)}%</td>
          <td>${(item.confidence*100).toFixed(1)}%</td></tr>`).join("");
      };
    </script>
  </body>
</html>"""


@app.get("/forecasts", response_model=ForecastResponse)
def forecasts(mode: DataMode = DataMode.snapshot) -> ForecastResponse:
    if mode is DataMode.online:
        raise HTTPException(status_code=501, detail="Online refresh is not enabled in the MVP")
    snapshot = load_csv_snapshot(_default_snapshot())
    feature_frame = build_features(snapshot.frame).dropna(subset=FEATURE_COLUMNS + ["target_positive"])
    if feature_frame.empty:
        raise HTTPException(status_code=422, detail="Snapshot does not contain enough history")
    model = BaselineModel(estimator=None).fit(feature_frame)
    latest = feature_frame.sort_values("date").groupby("symbol").tail(1)
    probabilities = model.predict_probability(latest)
    expected_returns = model.predict_expected_return(latest)
    generated_at = datetime.now(timezone.utc)
    items = [
        Forecast(
            symbol=row.symbol,
            as_of=row.date.date(),
            horizon_days=20,
            positive_return_probability=float(probability),
            expected_return=float(expected_return),
            expected_volatility=float(row.volatility_20d),
            confidence=float(abs(probability - 0.5) * 2),
            evidence=["20-day return momentum", "20-day volatility", "20-day volume ratio"],
            model_version=model.version,
            data_timestamp=snapshot.retrieved_at.to_pydatetime(),
        )
        for row, probability, expected_return in zip(
            latest.itertuples(), probabilities, expected_returns, strict=True
        )
    ]
    return ForecastResponse(
        mode=mode,
        generated_at=generated_at,
        forecasts=items,
        disclaimer=DISCLAIMER,
    )


@app.post("/recommendations", response_model=RecommendationResponse)
def recommendations(request: RecommendationRequest) -> RecommendationResponse:
    if not request.confirmed:
        raise HTTPException(
            status_code=400,
            detail="Explicit confirmation is required before generating recommendations",
        )
    return RecommendationResponse(
        confirmed=True,
        risk_profile=request.risk_profile,
        symbols=request.symbols,
        message=(
            f"Research shortlist generated for the {request.risk_profile.value} profile. "
            "Review the forecast evidence and risk metrics before acting."
        ),
    )
