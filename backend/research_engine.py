"""Deterministic, testable quantitative research engine for four assets."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

import numpy as np
import pandas as pd
from scipy.optimize import minimize

from .data_pipeline import ASSETS, load_metadata, load_returns


WINDOWS = {"10Y": 10, "20Y": 20, "30Y": 30, "40Y": 40, "50Y": 50}
METHODS = {
    "equal_weight": "Equal Weight",
    "max_sharpe": "Historical Max Sharpe",
    "min_volatility": "Minimum Volatility",
    "max_sortino": "Maximum Sortino",
    "max_calmar": "Maximum Calmar",
    "risk_balanced": "Risk-Balanced",
    "robust_quant": "Robust Quant Allocation",
}
COMPARISON_METHODS = ["equal_weight", "max_sharpe", "min_volatility", "max_sortino", "max_calmar", "robust_quant"]
DEFAULT_CONFIG = {
    "research_window": "30Y",
    "optimizer": "robust_quant",
    "frequency": "monthly",
    "rebalance_frequency": "monthly",
    "training_years": 10,
    "test_months": 12,
    "transaction_cost_bps": 10,
    "constraints": {"min_asset_weight": 0.0, "max_SP500": 0.80, "max_GOLD": 0.80, "max_LONG_TREASURY": 0.80, "max_CASH": 0.80},
    "robust_objective": {"sharpe": 0.25, "sortino": 0.20, "calmar": 0.15, "max_drawdown": 0.15, "cvar": 0.15, "turnover": 0.10},
    "robustness_weights": {
        "oos_sharpe": 0.20, "oos_sortino": 0.15, "oos_calmar": 0.15, "drawdown": 0.10,
        "cvar": 0.10, "weight_stability": 0.10, "turnover": 0.05, "window_stability": 0.10, "regime_stability": 0.05,
    },
    "monte_carlo_simulations": 1000,
    "seed": 42,
}


@dataclass
class ConstraintConfig:
    min_asset_weight: float = 0.0
    max_SP500: float = 0.80
    max_GOLD: float = 0.80
    max_LONG_TREASURY: float = 0.80
    max_CASH: float = 0.80

    @property
    def bounds(self) -> List[Tuple[float, float]]:
        return [(self.min_asset_weight, getattr(self, f"max_{asset}")) for asset in ASSETS]


def annualization_factor(frequency: str) -> int:
    if frequency == "daily":
        return 252
    if frequency == "monthly":
        return 12
    raise ValueError("frequency must be 'daily' or 'monthly'")


def _periods_per_year(index: pd.DatetimeIndex) -> float:
    if len(index) < 2:
        return 12.0
    median_days = float(np.median(np.diff(index.values).astype("timedelta64[D]").astype(float)))
    return 252.0 if median_days <= 3 else 12.0


def portfolio_return_series(returns: pd.DataFrame, weights: Sequence[float]) -> pd.Series:
    """Simple weighted-period portfolio return used by metric unit tests."""
    w = np.asarray(weights, dtype=float)
    if returns.shape[1] != len(w):
        raise ValueError("weights must have one entry per return column")
    if not np.isclose(w.sum(), 1.0):
        raise ValueError("weights must sum to one")
    return returns.dot(w).rename("portfolio")


def _is_rebalance_due(previous: pd.Timestamp, current: pd.Timestamp, frequency: str) -> bool:
    if frequency == "daily":
        return True
    if frequency == "monthly":
        return current.to_period("M") != previous.to_period("M")
    if frequency == "quarterly":
        return current.to_period("Q") != previous.to_period("Q")
    if frequency == "annual":
        return current.year != previous.year
    raise ValueError("rebalance_frequency must be daily, monthly, quarterly, or annual")


def portfolio_returns(
    returns: pd.DataFrame,
    weights: Sequence[float],
    rebalance_frequency: str = "monthly",
    transaction_cost_bps: float = 0.0,
) -> pd.DataFrame:
    """Apply target weights with drift, rebalancing, turnover, and costs."""
    returns = returns[ASSETS].dropna(how="any")
    w_target = np.asarray(weights, dtype=float)
    if len(w_target) != len(ASSETS) or not np.isclose(w_target.sum(), 1.0):
        raise ValueError("weights must contain four assets and sum to one")
    if np.any(w_target < -1e-10):
        raise ValueError("short selling is disabled")

    current = w_target.copy()
    wealth = 1.0
    rows = []
    for i, (date, row) in enumerate(returns.iterrows()):
        turnover = 0.0
        cost = 0.0
        if i > 0 and _is_rebalance_due(returns.index[i - 1], date, rebalance_frequency):
            turnover = float(np.abs(w_target - current).sum())
            cost = turnover * transaction_cost_bps / 10000.0
            wealth *= max(0.0, 1.0 - cost)
            current = w_target.copy()
        gross_return = float(np.dot(current, row.to_numpy(dtype=float)))
        start_wealth = wealth
        wealth *= max(0.0, 1.0 + gross_return)
        net_return = wealth / start_wealth - 1.0
        drift_denominator = max(1e-12, 1.0 + gross_return)
        current = current * (1.0 + row.to_numpy(dtype=float)) / drift_denominator
        rows.append((date, gross_return, net_return, turnover, cost, wealth))
    return pd.DataFrame(rows, columns=["date", "gross_return", "return", "turnover", "transaction_cost", "nav"]).set_index("date")


def _safe_ratio(numerator: float, denominator: float) -> Optional[float]:
    if denominator is None or not np.isfinite(denominator) or abs(denominator) < 1e-12:
        return None
    return float(numerator / denominator)


def calculate_metrics(
    returns: pd.Series | Sequence[float],
    frequency: Optional[str] = None,
    turnover: Optional[pd.Series] = None,
    risk_free_rate: float = 0.0,
) -> Dict[str, Optional[float]]:
    """Calculate independently testable return, risk, and behavior metrics."""
    series = pd.Series(returns).dropna().astype(float)
    if series.empty:
        return {"sample_size": 0}
    if frequency is None:
        factor = _periods_per_year(pd.DatetimeIndex(series.index)) if isinstance(series.index, pd.DatetimeIndex) else 12
    else:
        factor = annualization_factor(frequency)
    years = len(series) / factor
    nav = (1.0 + series).cumprod()
    cagr = float(nav.iloc[-1] ** (1.0 / max(years, 1e-12)) - 1.0) if nav.iloc[-1] > 0 else -1.0
    annualized_return = float((1.0 + series.mean()) ** factor - 1.0)
    volatility = float(series.std(ddof=1) * np.sqrt(factor)) if len(series) > 1 else 0.0
    rf_period = (1.0 + risk_free_rate) ** (1.0 / factor) - 1.0
    excess = series - rf_period
    downside = np.minimum(excess.to_numpy(), 0.0)
    downside_deviation = float(np.sqrt(np.mean(downside**2)) * np.sqrt(factor))
    sharpe = _safe_ratio(float(excess.mean() * factor), volatility)
    sortino = _safe_ratio(float(excess.mean() * factor), downside_deviation)
    drawdown = nav / nav.cummax() - 1.0
    max_drawdown = float(drawdown.min())
    var95 = float(series.quantile(0.05))
    tail = series[series <= var95]
    cvar95 = float(tail.mean()) if not tail.empty else var95
    calmar = _safe_ratio(cagr, abs(max_drawdown))
    annual_returns = (1.0 + series).groupby(series.index.year if isinstance(series.index, pd.DatetimeIndex) else np.arange(len(series)) // int(factor)).prod() - 1.0
    worst_year = float(annual_returns.min()) if len(annual_returns) else None
    best_year = float(annual_returns.max()) if len(annual_returns) else None
    negative_years = int((annual_returns < 0).sum()) if len(annual_returns) else 0
    trough = drawdown.idxmin()
    peak_level = nav.loc[:trough].max()
    after_trough = nav.loc[trough:]
    recovered = after_trough[after_trough >= peak_level]
    recovery_time = None
    if len(recovered):
        periods = len(series.loc[trough:recovered.index[0]]) - 1
        recovery_time = float(periods / factor)
    annual_turnover = None
    if turnover is not None:
        turnover_series = pd.Series(turnover).fillna(0.0)
        annual_turnover = float(turnover_series.sum() / max(years, 1e-12))
    return {
        "sample_size": int(len(series)),
        "years": float(years),
        "cagr": cagr,
        "annualized_return": annualized_return,
        "volatility": volatility,
        "max_drawdown": max_drawdown,
        "cvar95": cvar95,
        "var95": var95,
        "downside_deviation": downside_deviation,
        "sharpe": sharpe,
        "sortino": sortino,
        "calmar": calmar,
        "turnover": annual_turnover,
        "worst_year": worst_year,
        "best_year": best_year,
        "negative_years": negative_years,
        "recovery_time_years": recovery_time,
    }


def _constraint_config(config: Optional[Mapping[str, float | int]]) -> ConstraintConfig:
    config = config or {}
    return ConstraintConfig(**{key: float(value) for key, value in config.items() if key in ConstraintConfig.__dataclass_fields__})


def _feasible_initial(bounds: List[Tuple[float, float]]) -> np.ndarray:
    equal = np.repeat(1.0 / len(bounds), len(bounds))
    if all(low - 1e-9 <= value <= high + 1e-9 for value, (low, high) in zip(equal, bounds)):
        return equal
    result = minimize(lambda w: float(np.sum((w - equal) ** 2)), equal, method="SLSQP", bounds=bounds, constraints={"type": "eq", "fun": lambda w: np.sum(w) - 1.0})
    if not result.success:
        raise ValueError("constraints do not admit a feasible fully-invested portfolio")
    return result.x


def _metric_for_weights(returns: pd.DataFrame, weights: np.ndarray) -> Tuple[pd.Series, Dict[str, Optional[float]]]:
    # SLSQP can evaluate the objective at a slightly infeasible trial point
    # before enforcing the equality constraint. Normalize only inside the
    # objective; the returned allocation is checked and normalized later.
    trial = np.asarray(weights, dtype=float)
    if trial.sum() <= 0:
        trial = np.repeat(1.0 / len(trial), len(trial))
    else:
        trial = trial / trial.sum()
    port = portfolio_return_series(returns, trial)
    return port, calculate_metrics(port, frequency="monthly")


def _robust_score(metrics: Mapping[str, Optional[float]], objective: Mapping[str, float], turnover: float = 0.0) -> float:
    def value(key: str) -> float:
        value = metrics.get(key)
        return float(value) if value is not None and np.isfinite(value) else 0.0

    return (
        objective.get("sharpe", 0.25) * value("sharpe")
        + objective.get("sortino", 0.20) * value("sortino")
        + objective.get("calmar", 0.15) * value("calmar")
        - objective.get("max_drawdown", 0.15) * abs(value("max_drawdown"))
        - objective.get("cvar", 0.15) * abs(value("cvar95"))
        - objective.get("turnover", 0.10) * turnover
    )


def optimize_weights(
    returns: pd.DataFrame,
    method: str,
    constraints: Optional[Mapping[str, float | int]] = None,
    robust_objective: Optional[Mapping[str, float]] = None,
    previous_weights: Optional[Sequence[float]] = None,
) -> np.ndarray:
    """Optimize only on the passed training sample."""
    if method not in METHODS:
        raise ValueError(f"unknown optimizer: {method}")
    returns = returns[ASSETS].dropna(how="any")
    if len(returns) < 2:
        raise ValueError("at least two observations are required for optimization")
    c = _constraint_config(constraints)
    bounds = c.bounds
    x0 = _feasible_initial(bounds)
    if method == "equal_weight":
        return x0
    objective = robust_objective or DEFAULT_CONFIG["robust_objective"]
    prev = np.asarray(previous_weights if previous_weights is not None else x0, dtype=float)
    return_matrix = returns.to_numpy(dtype=float)
    mean_return = return_matrix.mean(axis=0)
    covariance = np.cov(return_matrix, rowvar=False) * 12.0
    covariance += np.eye(len(ASSETS)) * 1e-10

    def stats(w: np.ndarray) -> Dict[str, Optional[float]]:
        trial = np.asarray(w, dtype=float)
        if trial.sum() <= 0:
            trial = np.repeat(1.0 / len(trial), len(trial))
        else:
            trial = trial / trial.sum()
        series = return_matrix @ trial
        volatility = float(np.std(series, ddof=1) * np.sqrt(12))
        downside = np.minimum(series, 0.0)
        downside_deviation = float(np.sqrt(np.mean(downside**2)) * np.sqrt(12))
        log_nav = np.log1p(np.clip(series, -0.999999, None)).cumsum()
        nav = np.exp(log_nav - log_nav[0])
        cagr = float(np.exp(log_nav[-1] / max(len(series) / 12.0, 1e-12)) - 1.0)
        max_drawdown = float(np.min(nav / np.maximum.accumulate(nav) - 1.0))
        var95 = float(np.quantile(series, 0.05))
        tail = series[series <= var95]
        cvar95 = float(tail.mean()) if len(tail) else var95
        return {
            "sharpe": _safe_ratio(float(series.mean() * 12.0), volatility),
            "sortino": _safe_ratio(float(series.mean() * 12.0), downside_deviation),
            "calmar": _safe_ratio(cagr, abs(max_drawdown)),
            "volatility": volatility,
            "max_drawdown": max_drawdown,
            "cvar95": cvar95,
        }

    def objective_fn(w: np.ndarray) -> float:
        m = stats(w)
        if method == "max_sharpe":
            return -float(m.get("sharpe") or -1e6)
        if method == "min_volatility":
            return float(m.get("volatility") or 1e6)
        if method == "max_sortino":
            return -float(m.get("sortino") or -1e6)
        if method == "max_calmar":
            return -float(m.get("calmar") or -1e6)
        if method == "robust_quant":
            turnover = float(np.abs(w - prev).sum())
            return -_robust_score(m, objective, turnover)
        if method == "risk_balanced":
            marginal = covariance @ w
            contribution = w * marginal
            total = max(1e-12, float(w @ covariance @ w))
            contribution = contribution / total
            target = np.repeat(1.0 / len(w), len(w))
            return float(np.sum((contribution - target) ** 2))
        raise AssertionError(method)

    result = minimize(
        objective_fn,
        x0,
        method="SLSQP",
        bounds=bounds,
        constraints={"type": "eq", "fun": lambda w: float(np.sum(w) - 1.0)},
        options={"maxiter": 80, "ftol": 1e-7},
    )
    if not result.success:
        # A deterministic feasible fallback is preferable to reporting a
        # silently invalid allocation when a constrained objective is flat.
        return x0
    weights = np.clip(result.x, [b[0] for b in bounds], [b[1] for b in bounds])
    return weights / weights.sum()


def _monthly(returns: pd.DataFrame) -> pd.DataFrame:
    if len(returns) < 2:
        return returns.copy()
    factor = _periods_per_year(pd.DatetimeIndex(returns.index))
    if factor == 12:
        return returns
    return (1.0 + returns).resample("ME").prod() - 1.0


def walk_forward(
    returns: pd.DataFrame,
    method: str,
    training_years: int = 10,
    test_months: int = 12,
    constraints: Optional[Mapping[str, float | int]] = None,
    robust_objective: Optional[Mapping[str, float]] = None,
    transaction_cost_bps: float = 0.0,
) -> Dict[str, object]:
    """Rolling train/test blocks; each test block is unseen by its optimizer."""
    monthly = _monthly(returns[ASSETS].dropna(how="any"))
    rows: List[Dict[str, object]] = []
    oos_parts: List[pd.DataFrame] = []
    if len(monthly) < training_years * 12 + 1:
        return {"summary": calculate_metrics(pd.Series(dtype=float), frequency="monthly"), "windows": [], "oos_returns": []}
    test_start = monthly.index[0] + pd.DateOffset(years=training_years)
    while test_start <= monthly.index[-1]:
        train_start = test_start - pd.DateOffset(years=training_years)
        test_end = test_start + pd.DateOffset(months=test_months) - pd.offsets.MonthEnd(1)
        train = monthly.loc[(monthly.index >= train_start) & (monthly.index < test_start)]
        test = monthly.loc[(monthly.index >= test_start) & (monthly.index <= test_end)]
        if len(train) < training_years * 12 - 2 or test.empty:
            break
        weights = optimize_weights(train, method, constraints, robust_objective)
        test_port = portfolio_returns(test, weights, "monthly", transaction_cost_bps)
        oos_parts.append(test_port)
        is_metrics = calculate_metrics(portfolio_returns(train, weights, "monthly", 0.0)["return"], frequency="monthly")
        oos_metrics = calculate_metrics(test_port["return"], frequency="monthly", turnover=test_port["turnover"])
        rows.append({
            "train_start": str(train.index[0].date()), "train_end": str(train.index[-1].date()),
            "test_start": str(test.index[0].date()), "test_end": str(test.index[-1].date()),
            "weights": {asset: float(weight) for asset, weight in zip(ASSETS, weights)},
            "is": is_metrics, "oos": oos_metrics,
        })
        test_start = test_end + pd.offsets.MonthEnd(1)
    combined = pd.concat(oos_parts).sort_index() if oos_parts else pd.DataFrame()
    summary = calculate_metrics(combined["return"], frequency="monthly", turnover=combined["turnover"]) if not combined.empty else {"sample_size": 0}
    return {"summary": summary, "windows": rows, "oos_returns": _series_points(combined["return"]) if not combined.empty else []}


def stability_analysis(
    returns: pd.DataFrame,
    end_date: pd.Timestamp,
    method: str,
    constraints: Optional[Mapping[str, float | int]],
    robust_objective: Optional[Mapping[str, float]],
) -> Dict[str, object]:
    monthly = _monthly(returns[ASSETS].dropna(how="any"))
    rows = []
    for label, years in WINDOWS.items():
        end = min(pd.Timestamp(end_date), monthly.index[-1])
        start = end - pd.DateOffset(years=years) + pd.offsets.MonthEnd(1)
        sample = monthly.loc[(monthly.index >= start) & (monthly.index <= end)]
        expected = years * 12
        if len(sample) < max(24, expected - 2):
            rows.append({"window": label, "available": False, "observations": int(len(sample))})
            continue
        weights = optimize_weights(sample, method, constraints, robust_objective)
        rows.append({"window": label, "available": True, "observations": int(len(sample)), "start_date": str(sample.index[0].date()), "end_date": str(sample.index[-1].date()), "weights": {a: float(w) for a, w in zip(ASSETS, weights)}})
    available = [row for row in rows if row.get("available")]
    if available:
        matrix = np.array([[row["weights"][asset] for asset in ASSETS] for row in available])
        mean = matrix.mean(axis=0)
        std = matrix.std(axis=0, ddof=0)
        min_weight = matrix.min(axis=0)
        max_weight = matrix.max(axis=0)
        cv = np.divide(std, np.abs(mean), out=np.full_like(std, np.nan), where=np.abs(mean) > 1e-12)
        dispersion = float(std.mean())
        score = float(np.clip(1.0 - dispersion / 0.25, 0.0, 1.0))
        label = "High" if score >= 0.75 else "Medium" if score >= 0.50 else "Low"
        summary = {"mean_weight": dict(zip(ASSETS, mean)), "std_weight": dict(zip(ASSETS, std)), "min_weight": dict(zip(ASSETS, min_weight)), "max_weight": dict(zip(ASSETS, max_weight)), "coefficient_of_variation": dict(zip(ASSETS, cv)), "score": score, "label": label, "formula": "clip(1 - mean(asset weight std) / 0.25, 0, 1)"}
    else:
        summary = {"score": None, "label": "Unavailable", "formula": "clip(1 - mean(asset weight std) / 0.25, 0, 1)"}
    return {"rows": rows, "summary": summary}


def rolling_allocations(
    returns: pd.DataFrame,
    method: str,
    selected_start: pd.Timestamp,
    selected_end: pd.Timestamp,
    constraints: Optional[Mapping[str, float | int]],
    robust_objective: Optional[Mapping[str, float]],
) -> List[Dict[str, object]]:
    monthly = _monthly(returns[ASSETS].dropna(how="any"))
    rows = []
    for label, years in [("10Y", 10), ("20Y", 20), ("30Y", 30)]:
        window_months = years * 12
        for i in range(window_months, len(monthly)):
            date = monthly.index[i]
            if date < selected_start or date > selected_end:
                continue
            train = monthly.iloc[i - window_months:i]
            weights = optimize_weights(train, method, constraints, robust_objective)
            rows.append({"date": str(date.date()), "window": label, **{asset: float(weight) for asset, weight in zip(ASSETS, weights)}})
    return rows


def coupling_analysis(returns: pd.DataFrame, rolling_windows: Sequence[int] = (36, 60, 120)) -> Dict[str, object]:
    monthly = _monthly(returns[ASSETS].dropna(how="any"))
    latest = monthly.corr()
    rolling = {}
    pairs = [("SP500", asset) for asset in ASSETS if asset != "SP500"]
    for window in rolling_windows:
        points = []
        for i in range(window - 1, len(monthly)):
            chunk = monthly.iloc[i - window + 1:i + 1]
            corr = chunk.corr()
            points.append({"date": str(monthly.index[i].date()), **{f"{a}_{b}": _finite(corr.loc[a, b]) for a, b in pairs}})
        rolling[str(window)] = points
    covariance = monthly.cov()
    beta = pd.DataFrame(index=monthly.index, columns=ASSETS, dtype=float)
    market = monthly["SP500"]
    for asset in ASSETS:
        beta[asset] = monthly[asset].rolling(60).cov(market) / market.rolling(60).var()
    eigenvalues, eigenvectors = np.linalg.eigh(covariance.to_numpy(dtype=float))
    order = np.argsort(eigenvalues)[::-1]
    eigenvalues = eigenvalues[order]
    eigenvectors = eigenvectors[:, order]
    variance_explained = eigenvalues / max(1e-12, eigenvalues.sum())
    min_direction = eigenvectors[:, -1]
    min_direction = min_direction / max(1e-12, np.abs(min_direction).sum())
    return {
        "latest_correlation": _matrix_json(latest, ASSETS),
        "latest_covariance": _matrix_json(covariance, ASSETS),
        "rolling_correlation": rolling,
        "rolling_beta": [_row_json(date, beta.loc[date]) for date in beta.dropna().index],
        "pca": {"components": [f"PC{i + 1}" for i in range(len(ASSETS))], "eigenvalues": [_finite(x) for x in eigenvalues], "variance_explained": [_finite(x) for x in variance_explained], "eigenvectors": eigenvectors.tolist(), "minimum_variance_eigen_direction": dict(zip(ASSETS, min_direction))},
    }


def regime_analysis(returns: pd.DataFrame, weights_by_method: Mapping[str, Sequence[float]]) -> Dict[str, object]:
    monthly = _monthly(returns[ASSETS].dropna(how="any"))
    equity_momentum = (1.0 + monthly["SP500"]).rolling(12).apply(np.prod, raw=True) - 1.0
    gold_momentum = (1.0 + monthly["GOLD"]).rolling(12).apply(np.prod, raw=True) - 1.0
    bond_momentum = (1.0 + monthly["LONG_TREASURY"]).rolling(12).apply(np.prod, raw=True) - 1.0
    equity_3m = (1.0 + monthly["SP500"]).rolling(3).apply(np.prod, raw=True) - 1.0
    volatility = monthly["SP500"].rolling(12).std() * np.sqrt(12)
    labels = []
    for date in monthly.index:
        e12, g12, b12, e3, vol = [float(x) if np.isfinite(x) else np.nan for x in (equity_momentum.loc[date], gold_momentum.loc[date], bond_momentum.loc[date], equity_3m.loc[date], volatility.loc[date])]
        if np.isfinite(e3) and (e3 < -0.15 or (e3 < 0 and np.isfinite(vol) and vol > 0.35)):
            regime = "Crisis"
        elif np.isfinite(e12) and np.isfinite(b12) and e12 < 0 and b12 > 0:
            regime = "Deflation"
        elif np.isfinite(e12) and np.isfinite(g12) and np.isfinite(b12) and g12 > e12 and b12 < 0:
            regime = "Inflation"
        elif np.isfinite(e12) and e12 < 0:
            regime = "Risk-Off"
        elif np.isfinite(e12) and e12 > 0 and np.isfinite(vol) and vol < 0.20:
            regime = "Risk-On"
        else:
            regime = "Neutral"
        labels.append(regime)
    labels = pd.Series(labels, index=monthly.index, name="regime")
    # A label computed at month t is applied to month t+1.  This prevents the
    # current month's realized return from being used to classify itself.
    applied = labels.shift(1)
    rows = []
    for regime in ["Risk-On", "Risk-Off", "Inflation", "Deflation", "Crisis", "Neutral"]:
        sample = monthly.loc[applied == regime]
        if sample.empty:
            continue
        row = {"regime": regime, "observations": int(len(sample))}
        for method, weights in weights_by_method.items():
            row[method] = calculate_metrics(portfolio_return_series(sample, weights), frequency="monthly")
        rows.append(row)
    robust_cagrs = [row["robust_quant"].get("cagr") for row in rows if row.get("robust_quant", {}).get("cagr") is not None]
    regime_dispersion = float(np.std(robust_cagrs)) if robust_cagrs else 0.25
    stability = float(np.clip(1.0 - regime_dispersion / 0.25, 0.0, 1.0))
    return {"classification": "Rule-Based Macro Regime", "feature_notes": "Trailing equity/gold/bond momentum and equity volatility; label is shifted one month before performance attribution.", "timeline": [{"date": str(date.date()), "regime": str(labels.loc[date])} for date in labels.dropna().index], "performance": rows, "stability_score": stability, "stability_formula": "clip(1 - std(regime Robust Quant CAGR) / 0.25, 0, 1)"}


def crisis_analysis(returns: pd.DataFrame, weights_by_method: Mapping[str, Sequence[float]]) -> List[Dict[str, object]]:
    monthly = _monthly(returns[ASSETS].dropna(how="any"))
    periods = {
        "1987 crash": ("1987-08-01", "1987-12-31"),
        "2000-2002 dot-com": ("2000-03-01", "2002-10-31"),
        "2008-2009 GFC": ("2008-09-01", "2009-03-31"),
        "2020 COVID": ("2020-02-01", "2020-04-30"),
        "2022 inflation / rates shock": ("2022-01-01", "2022-10-31"),
    }
    rows = []
    for name, (start, end) in periods.items():
        sample = monthly.loc[start:end]
        if sample.empty:
            continue
        row = {"period": name, "start_date": str(sample.index[0].date()), "end_date": str(sample.index[-1].date()), "observations": int(len(sample))}
        for asset in ASSETS:
            row[asset] = float((1.0 + sample[asset]).prod() - 1.0)
        for method, weights in weights_by_method.items():
            port = portfolio_returns(sample, weights, "monthly", 0.0)
            metrics = calculate_metrics(port["return"], frequency="monthly")
            row[method] = {"return": float((1.0 + port["return"]).prod() - 1.0), "max_drawdown": metrics.get("max_drawdown"), "recovery_time_years": metrics.get("recovery_time_years")}
        rows.append(row)
    return rows


def transaction_sensitivity(returns: pd.DataFrame, weights: Sequence[float], rebalance_frequency: str) -> List[Dict[str, object]]:
    rows = []
    for bps in [0, 5, 10, 25, 50]:
        port = portfolio_returns(returns, weights, rebalance_frequency, bps)
        metrics = calculate_metrics(port["return"], turnover=port["turnover"])
        rows.append({"transaction_cost_bps": bps, "gross_cagr": calculate_metrics(port["gross_return"])["cagr"], "net_cagr": metrics["cagr"], "gross_sharpe": calculate_metrics(port["gross_return"])["sharpe"], "net_sharpe": metrics["sharpe"], "turnover": metrics["turnover"]})
    return rows


def monte_carlo_block_bootstrap(portfolio_returns_series: pd.Series, simulations: int = 1000, block_size: int = 6, seed: int = 42) -> Dict[str, object]:
    series = pd.Series(portfolio_returns_series).dropna().astype(float).to_numpy()
    if len(series) < block_size * 2:
        return {"simulations": 0, "error": "not enough observations for block bootstrap"}
    rng = np.random.default_rng(seed)
    n = len(series)
    metrics = []
    blocks = [series[i:i + block_size] for i in range(n - block_size + 1)]
    for _ in range(int(simulations)):
        sample = []
        while len(sample) < n:
            sample.extend(blocks[int(rng.integers(0, len(blocks)))])
        sample = np.asarray(sample[:n])
        m = calculate_metrics(pd.Series(sample), frequency="monthly")
        metrics.append([m.get("sharpe", 0.0) or 0.0, m.get("cagr", -1.0) or -1.0, m.get("max_drawdown", -1.0) or -1.0])
    arr = np.asarray(metrics)
    return {
        "simulations": int(simulations), "block_size_months": int(block_size), "seed": int(seed),
        "sharpe_distribution": _distribution(arr[:, 0]), "cagr_distribution": _distribution(arr[:, 1]), "max_drawdown_distribution": _distribution(arr[:, 2]),
        "probability_negative_cagr": float(np.mean(arr[:, 1] < 0)), "probability_sharpe_below_zero": float(np.mean(arr[:, 0] < 0)),
    }


def robustness_score(oos: Mapping[str, Optional[float]], stability: Mapping[str, object], regime_stability: float, turnover: Optional[float], weights: Optional[Mapping[str, float]] = None) -> Dict[str, object]:
    coefficients = weights or DEFAULT_CONFIG["robustness_weights"]
    def clip01(value: Optional[float]) -> float:
        return float(np.clip(value if value is not None and np.isfinite(value) else 0.0, 0.0, 1.0))
    components = {
        "oos_sharpe": clip01(((oos.get("sharpe") or 0.0) + 1.0) / 2.0),
        "oos_sortino": clip01(((oos.get("sortino") or 0.0) + 1.0) / 2.0),
        "oos_calmar": clip01(((oos.get("calmar") or 0.0) + 0.5) / 1.5),
        "drawdown": clip01(1.0 - abs(oos.get("max_drawdown") or 1.0) / 0.50),
        "cvar": clip01(1.0 - abs(oos.get("cvar95") or 1.0) / 0.10),
        "weight_stability": clip01(stability.get("summary", {}).get("score")),
        "turnover": clip01(1.0 - (turnover or 1.0) / 1.0),
        "window_stability": clip01(stability.get("summary", {}).get("score")),
        "regime_stability": clip01(regime_stability),
    }
    score = float(sum(float(coefficients.get(key, 0.0)) * value for key, value in components.items()))
    return {"score": score, "components": components, "coefficients": coefficients, "formula": "weighted sum of normalized OOS risk-adjusted return, drawdown, CVaR, turnover, window stability, and regime stability components; coefficients sum to 1.0 by default."}


def run_research(data_dir: Path, config: Optional[Mapping[str, object]] = None) -> Dict[str, object]:
    cfg = json.loads(json.dumps(DEFAULT_CONFIG))
    if config:
        for key, value in config.items():
            if isinstance(value, Mapping) and isinstance(cfg.get(key), dict):
                cfg[key].update(value)
            else:
                cfg[key] = value
    frequency = str(cfg["frequency"])
    full = load_returns(data_dir, frequency)
    metadata = load_metadata(data_dir)
    if full.empty:
        raise ValueError("return panel is empty")
    end_date = pd.Timestamp(cfg.get("end_date") or full.index[-1])
    if cfg.get("start_date"):
        start_date = pd.Timestamp(cfg["start_date"])
    else:
        start_date = end_date - pd.DateOffset(years=WINDOWS[str(cfg["research_window"])]) + pd.offsets.Day(1)
    analysis = full.loc[(full.index >= start_date) & (full.index <= end_date)]
    if len(analysis) < 24:
        raise ValueError("selected research window has fewer than 24 common observations")
    strategic = _monthly(analysis)
    full_strategic = _monthly(full.loc[full.index <= end_date])
    constraints = cfg.get("constraints", {})
    robust_objective = cfg.get("robust_objective", {})
    optimizer = str(cfg["optimizer"])
    method_weights = {method: optimize_weights(strategic, method, constraints, robust_objective) for method in METHODS}
    selected_weights = method_weights[optimizer]
    selected_port = portfolio_returns(analysis, selected_weights, str(cfg["rebalance_frequency"]), float(cfg["transaction_cost_bps"]))
    selected_gross = portfolio_returns(analysis, selected_weights, str(cfg["rebalance_frequency"]), 0.0)
    is_metrics = calculate_metrics(selected_port["return"], frequency=frequency, turnover=selected_port["turnover"])
    gross_metrics = calculate_metrics(selected_gross["return"], frequency=frequency, turnover=selected_gross["turnover"])
    # Walk-forward needs a history longer than the selected IS window in order
    # to produce an OOS sample for 10Y/20Y views. It runs through the selected
    # endpoint on the full available monthly panel; the headline allocation
    # remains tied to the chosen research window.
    walk = walk_forward(full_strategic, optimizer, int(cfg["training_years"]), int(cfg["test_months"]), constraints, robust_objective, float(cfg["transaction_cost_bps"]))
    oos_metrics = walk["summary"]
    stability = stability_analysis(full_strategic, end_date, optimizer, constraints, robust_objective)
    rolling = rolling_allocations(full_strategic, optimizer, start_date, end_date, constraints, robust_objective)
    coupling = coupling_analysis(strategic)
    # Stress periods and regime diagnostics use the full available monthly
    # panel so a selected 30Y endpoint does not accidentally hide 1987 or the
    # dot-com period. The selected window still governs the headline allocation.
    crisis = crisis_analysis(full_strategic, {method: method_weights[method] for method in COMPARISON_METHODS})
    regimes = regime_analysis(full_strategic, {method: method_weights[method] for method in COMPARISON_METHODS})
    transactions = transaction_sensitivity(analysis, selected_weights, str(cfg["rebalance_frequency"]))
    monte_carlo = monte_carlo_block_bootstrap(selected_port["return"], int(cfg["monte_carlo_simulations"]), 6, int(cfg["seed"]))
    robust = robustness_score(oos_metrics, stability, float(regimes.get("stability_score", 0.0)), is_metrics.get("turnover"), cfg.get("robustness_weights"))

    return {
        "research_window": cfg["research_window"], "optimizer": optimizer, "optimizer_label": METHODS[optimizer], "frequency": frequency,
        "rebalance_frequency": cfg["rebalance_frequency"], "start_date": str(analysis.index[0].date()), "end_date": str(analysis.index[-1].date()),
        "sample_size": int(len(analysis)), "assumptions": ["No leverage; no short selling; weights sum to 100%.", "Monthly strategic optimization; test windows are unseen by their optimizer.", "Long Treasury is an explicitly labeled duration/yield proxy, not a canonical total-return index.", "Rule-based regimes are diagnostic labels, not objectively discovered states."],
        "provenance": metadata,
        "weights": {asset: float(weight) for asset, weight in zip(ASSETS, selected_weights)},
        "in_sample": is_metrics, "gross_in_sample": gross_metrics, "out_of_sample": oos_metrics,
        "robustness": robust, "stability": stability,
        "walk_forward": {"training_years": cfg["training_years"], "test_months": cfg["test_months"], "windows": walk["windows"]},
        "optimization": {"methods": {method: {"label": METHODS[method], "weights": {asset: float(weight) for asset, weight in zip(ASSETS, method_weights[method])}} for method in METHODS}, "objective": robust_objective, "constraints": constraints},
        "allocation_across_windows": stability["rows"], "rolling_allocations": rolling,
        "equity_curves": {method: _series_points((1.0 + portfolio_returns(analysis, method_weights[method], str(cfg["rebalance_frequency"]), 0.0)["return"]).cumprod()) for method in COMPARISON_METHODS},
        "drawdown_curves": {method: _series_points(((1.0 + portfolio_returns(analysis, method_weights[method], str(cfg["rebalance_frequency"]), 0.0)["return"]).cumprod() / (1.0 + portfolio_returns(analysis, method_weights[method], str(cfg["rebalance_frequency"]), 0.0)["return"]).cumprod().cummax() - 1.0)) for method in COMPARISON_METHODS},
        "rolling_sharpe": {method: _rolling_sharpe(analysis, method_weights[method], frequency) for method in COMPARISON_METHODS},
        "coupling": coupling, "regimes": regimes, "crisis": crisis, "transaction_sensitivity": transactions, "monte_carlo": monte_carlo,
        "data_quality": {"daily_common_start": metadata.get("daily_first_date"), "daily_common_end": metadata.get("daily_last_date"), "monthly_common_start": metadata.get("monthly_first_date"), "monthly_common_end": metadata.get("monthly_last_date"), "notes": metadata.get("missing_data", {}).get("notes")},
    }


def _finite(value: object) -> Optional[float]:
    try:
        value = float(value)
        return value if np.isfinite(value) else None
    except (TypeError, ValueError):
        return None


def _row_json(date: pd.Timestamp, row: pd.Series) -> Dict[str, object]:
    return {"date": str(pd.Timestamp(date).date()), **{str(key): _finite(value) for key, value in row.items()}}


def _matrix_json(frame: pd.DataFrame, labels: Sequence[str]) -> Dict[str, object]:
    return {"labels": list(labels), "values": [[_finite(frame.loc[row, col]) for col in labels] for row in labels]}


def _series_points(series: pd.Series, max_points: int = 600) -> List[Dict[str, object]]:
    series = pd.Series(series).dropna()
    if len(series) > max_points:
        positions = np.linspace(0, len(series) - 1, max_points).astype(int)
        series = series.iloc[np.unique(positions)]
    return [{"date": str(pd.Timestamp(date).date()), "value": _finite(value)} for date, value in series.items()]


def _rolling_sharpe(returns: pd.DataFrame, weights: Sequence[float], frequency: str, window: int = 36) -> List[Dict[str, object]]:
    port = portfolio_return_series(returns[ASSETS], weights)
    factor = annualization_factor(frequency)
    result = port.rolling(window).mean() / port.rolling(window).std() * np.sqrt(factor)
    return _series_points(result)


def _distribution(values: np.ndarray) -> Dict[str, float]:
    return {"p05": float(np.quantile(values, 0.05)), "p25": float(np.quantile(values, 0.25)), "median": float(np.quantile(values, 0.50)), "p75": float(np.quantile(values, 0.75)), "p95": float(np.quantile(values, 0.95))}


def cache_key(config: Mapping[str, object]) -> str:
    payload = json.dumps(config, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:20]
