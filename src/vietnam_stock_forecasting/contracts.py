from datetime import date, datetime
from enum import Enum

from pydantic import BaseModel, Field


class DataMode(str, Enum):
    snapshot = "snapshot"
    online = "online"


class RiskProfile(str, Enum):
    conservative = "conservative"
    balanced = "balanced"
    growth = "growth"


class Forecast(BaseModel):
    symbol: str = Field(min_length=1, max_length=12)
    as_of: date
    horizon_days: int = Field(gt=0)
    positive_return_probability: float = Field(ge=0, le=1)
    expected_return: float
    expected_volatility: float = Field(ge=0)
    confidence: float = Field(ge=0, le=1)
    evidence: list[str] = Field(default_factory=list)
    model_version: str
    data_timestamp: datetime


class ForecastResponse(BaseModel):
    mode: DataMode
    generated_at: datetime
    forecasts: list[Forecast]
    disclaimer: str
    # Provenance of the snapshot behind `forecasts`; see docs/CONTRACTS_VI.md section 4.
    # Optional so that consumers written against the earlier contract keep working.
    source: str | None = None
    data_start: date | None = None
    data_end: date | None = None
    snapshot_retrieved_at: datetime | None = None
    warnings: list[str] = Field(default_factory=list)


class RecommendationRequest(BaseModel):
    confirmed: bool
    risk_profile: RiskProfile
    symbols: list[str] = Field(min_length=1)


class RecommendationResponse(BaseModel):
    confirmed: bool
    risk_profile: RiskProfile
    symbols: list[str]
    message: str
