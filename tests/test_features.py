import numpy as np
import pandas as pd
import pytest

from vietnam_stock_forecasting.features import FEATURE_COLUMNS, build_features


def test_features_are_grouped_by_symbol_and_target_uses_future_rows():
    frame = pd.DataFrame(
        {
            "date": pd.date_range("2024-01-01", periods=25),
            "symbol": ["AAA"] * 25,
            "close": range(100, 125),
            "volume": [1000] * 25,
        }
    )
    result = build_features(frame, horizon_days=5)
    assert result.loc[0, "return_5d"] != result.loc[24, "return_5d"]
    assert not pd.isna(result.loc[19, "forward_return"])
    assert pd.isna(result.loc[24, "forward_return"])


def test_invalid_horizon_is_rejected():
    with pytest.raises(ValueError, match="horizon_days"):
        build_features(pd.DataFrame(), horizon_days=0)


def test_volatility_calculation_isolated_per_symbol():
    """Kiểm tra độ biến động của từng mã là độc lập, không bị rò rỉ qua mã khác."""
    dates = pd.date_range("2024-01-01", periods=30)
    # Mã AAA giá nhảy loạn xạ từ 10 đến 500
    df_aaa = pd.DataFrame({
        "date": dates,
        "symbol": "AAA",
        "close": [10, 50, 20, 200, 30, 400] * 5,
        "volume": 1000,
    })
    # Mã BBB giá hoàn toàn đi ngang cố định 100
    df_bbb = pd.DataFrame({
        "date": dates,
        "symbol": "BBB",
        "close": [100.0] * 30,
        "volume": 1000,
    })
    combined = pd.concat([df_aaa, df_bbb], ignore_index=True)
    res = build_features(combined)

    bbb_vol = res[res["symbol"] == "BBB"]["volatility_20d"].dropna()
    # Do BBB giá không đổi, pct_change = 0 nên độ lệch chuẩn (volatility) phải bằng 0
    assert len(bbb_vol) > 0
    assert np.allclose(bbb_vol, 0.0)


def test_features_order_and_index_invariance():
    """Kiểm tra đầu vào bị xáo trộn thứ tự dòng thì kết quả features vẫn chính xác 100%."""
    dates = pd.date_range("2024-01-01", periods=25)
    df_a = pd.DataFrame({"date": dates, "symbol": "AAA", "close": np.linspace(50, 100, 25), "volume": 1000})
    df_b = pd.DataFrame({"date": dates, "symbol": "BBB", "close": np.linspace(100, 80, 25), "volume": 2000})
    normal_df = pd.concat([df_a, df_b], ignore_index=True)
    
    # Xáo trộn thứ tự dòng ngẫu nhiên và đảo lộn chỉ mục (index)
    shuffled_df = normal_df.sample(frac=1.0, random_state=42)

    res_normal = build_features(normal_df, horizon_days=5).sort_values(["symbol", "date"]).reset_index(drop=True)
    res_shuffled = build_features(shuffled_df, horizon_days=5).sort_values(["symbol", "date"]).reset_index(drop=True)

    for col in FEATURE_COLUMNS + ["forward_return"]:
        np.testing.assert_allclose(
            res_normal[col].fillna(-999).to_numpy(),
            res_shuffled[col].fillna(-999).to_numpy(),
            err_msg=f"Cột {col} bị lệch khi xáo trộn thứ tự dòng đầu vào!",
        )


def test_target_positive_preserves_na_alignment():
    """Kiểm tra 20 phiên cuối mỗi mã có target_positive là NA, không bị gán nhầm số 0."""
    dates = pd.date_range("2024-01-01", periods=30)
    df = pd.DataFrame({
        "date": dates,
        "symbol": "AAA",
        "close": np.linspace(10, 40, 30),
        "volume": 1000,
    })
    res = build_features(df, horizon_days=5)

    # 5 phiên cuối không có forward_return nên phải là NaN
    assert res["forward_return"].iloc[-5:].isna().all()
    # 5 phiên cuối target_positive bắt buộc phải là NA, không phải số 0
    assert res["target_positive"].iloc[-5:].isna().all()
    # Số lượng NA ở 2 cột phải khớp tuyệt đối
    assert res["target_positive"].isna().sum() == res["forward_return"].isna().sum()
    # Các phiên trước đó khi giá tăng thì target_positive là 1
    assert (res["target_positive"].iloc[:-5] == 1).all()


def test_no_look_ahead_leakage_on_past_features():
    """Kiểm tra thay đổi giá tương lai tuyệt đối không làm thay đổi feature tại quá khứ."""
    dates = pd.date_range("2024-01-01", periods=40)
    df1 = pd.DataFrame({
        "date": dates,
        "symbol": "AAA",
        "close": np.linspace(100, 140, 40),
        "volume": 1000,
    })
    df2 = df1.copy()
    # Thay đổi mạnh giá ở 10 phiên cuối (tương lai)
    df2.loc[30:, "close"] = df2.loc[30:, "close"] * 5

    res1 = build_features(df1, horizon_days=10)
    res2 = build_features(df2, horizon_days=10)

    # Kiểm tra tại mốc phiên 25 (quá khứ), toàn bộ features giữa df1 và df2 phải giống hệt nhau
    for col in FEATURE_COLUMNS:
        assert res1.loc[25, col] == res2.loc[25, col], f"Feature {col} tại t=25 bị ảnh hưởng bởi giá tương lai!"


def test_real_vn30_snapshot_features_integration():
    """Kiểm thử tích hợp thực tế với snapshot 30 mã VN30 (vn30_snapshot.csv)."""
    snapshot_path = "data/sample/vn30_snapshot.csv"
    df = pd.read_csv(snapshot_path)
    res = build_features(df, horizon_days=20)

    # Kiểm tra đủ 30 mã cổ phiếu
    assert res["symbol"].nunique() == 30
    # Kiểm tra số dòng khớp nguyên vẹn
    assert len(res) == len(df)
    # Kiểm tra các cột bắt buộc có mặt
    for col in FEATURE_COLUMNS + ["forward_return", "target_positive"]:
        assert col in res.columns
    # Kiểm tra số dòng NA giữa target_positive và forward_return bằng nhau chính xác
    assert res["target_positive"].isna().sum() == res["forward_return"].isna().sum()
