from fastapi.testclient import TestClient

from vietnam_stock_forecasting.api import app


client = TestClient(app)


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
