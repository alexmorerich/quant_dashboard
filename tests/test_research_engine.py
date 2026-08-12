from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from backend.research_engine import (
    ASSETS,
    calculate_metrics,
    optimize_weights,
    portfolio_return_series,
    portfolio_returns,
    run_research,
    transaction_sensitivity,
    walk_forward,
)


def synthetic_returns(months: int = 180) -> pd.DataFrame:
    rng = np.random.default_rng(7)
    dates = pd.date_range("2000-01-31", periods=months, freq="ME")
    base = rng.normal(0.006, 0.035, size=(months, 4))
    base[:, 0] += 0.003
    base[:, 3] = rng.normal(0.002, 0.002, size=months)
    return pd.DataFrame(base, index=dates, columns=ASSETS)


def test_cagr_and_volatility() -> None:
    returns = pd.Series([0.01] * 12, index=pd.date_range("2020-01-31", periods=12, freq="ME"))
    metrics = calculate_metrics(returns, frequency="monthly")
    assert metrics["cagr"] == pytest.approx((1.01**12) - 1, rel=1e-8)
    assert metrics["volatility"] == pytest.approx(0.0)


def test_risk_metrics_and_drawdown() -> None:
    returns = pd.Series([0.10, -0.20, 0.10, 0.0], index=pd.date_range("2020-01-31", periods=4, freq="ME"))
    metrics = calculate_metrics(returns, frequency="monthly")
    assert metrics["max_drawdown"] == pytest.approx(-0.20, abs=1e-8)
    assert metrics["cvar95"] <= metrics["var95"]
    assert metrics["sortino"] is not None
    assert metrics["calmar"] is not None
    assert metrics["negative_years"] == 1


def test_portfolio_return_and_covariance() -> None:
    returns = synthetic_returns(24)
    weights = np.repeat(0.25, 4)
    portfolio = portfolio_return_series(returns, weights)
    assert len(portfolio) == 24
    assert portfolio.iloc[0] == pytest.approx(float(returns.iloc[0].mean()))
    assert returns.cov().shape == (4, 4)


def test_constraints_and_optimizers() -> None:
    returns = synthetic_returns(120)
    constraints = {"min_asset_weight": 0.0, "max_SP500": 0.8, "max_GOLD": 0.8, "max_LONG_TREASURY": 0.8, "max_CASH": 0.8}
    for method in ["equal_weight", "max_sharpe", "min_volatility", "max_sortino", "max_calmar", "risk_balanced", "robust_quant"]:
        weights = optimize_weights(returns, method, constraints)
        assert weights.sum() == pytest.approx(1.0)
        assert np.all(weights >= -1e-8)
        assert np.all(weights <= 0.8 + 1e-8)


def test_transaction_costs_and_turnover() -> None:
    returns = synthetic_returns(24)
    weights = np.repeat(0.25, 4)
    gross = portfolio_returns(returns, weights, "monthly", 0.0)
    net = portfolio_returns(returns, weights, "monthly", 50.0)
    assert gross["turnover"].iloc[0] == 0.0
    assert net["transaction_cost"].sum() >= 0.0
    assert net["nav"].iloc[-1] <= gross["nav"].iloc[-1]
    sensitivity = transaction_sensitivity(returns, weights, "monthly")
    assert [row["transaction_cost_bps"] for row in sensitivity] == [0, 5, 10, 25, 50]


def test_walk_forward_splits_unseen_data() -> None:
    returns = synthetic_returns(180)
    result = walk_forward(returns, "equal_weight", training_years=5, test_months=12)
    assert result["windows"]
    first = result["windows"][0]
    assert first["train_end"] < first["test_start"]
    assert first["is"]["sample_size"] >= 58
    assert first["oos"]["sample_size"] <= 12


def test_end_to_end_result(tmp_path: Path) -> None:
    returns = synthetic_returns(180)
    returns.to_csv(tmp_path / "returns_monthly.csv", index_label="date")
    # Run the engine from a monthly panel; metadata mirrors the production data contract.
    metadata = {"retrieved_at": "test", "monthly_first_date": "2000-01-31", "monthly_last_date": "2014-12-31", "daily_first_date": "2000-01-31", "daily_last_date": "2014-12-31", "missing_data": {"notes": "test"}, "assets": {asset: {"instrument": asset, "source": "synthetic", "return_type": "test", "start_date": "2000-01-31", "end_date": "2014-12-31", "proxy_status": "test", "notes": "test"} for asset in ASSETS}}
    (tmp_path / "metadata.json").write_text(json.dumps(metadata))
    result = run_research(tmp_path, {"research_window": "10Y", "optimizer": "robust_quant", "monte_carlo_simulations": 25})
    assert set(result["weights"]) == set(ASSETS)
    assert result["in_sample"]["sample_size"] > 0
    assert "out_of_sample" in result
    assert "coupling" in result and "pca" in result["coupling"]
    assert "regimes" in result and "transaction_sensitivity" in result
