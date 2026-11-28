"""Portfolio exposures, historical tail losses, and deterministic shocks."""

import math

import numpy as np
import pandas as pd


BASE_WEIGHTS = {"SPY": 0.60, "IEF": 0.30, "GLD": 0.10}
SYMBOLS = tuple(BASE_WEIGHTS)
SESSIONS_PER_YEAR = 252
LOOKBACK = 126
CONFIDENCE = 0.95
DAILY_ES_LIMIT = 0.01
SCENARIOS = {
    "equity_selloff": {"SPY": -0.25, "IEF": 0.06, "GLD": 0.05},
    "inflation_and_rates": {"SPY": -0.15, "IEF": -0.12, "GLD": -0.05},
    "gold_selloff": {"SPY": -0.04, "IEF": 0.02, "GLD": -0.25},
}


def expected_tail_loss(returns, confidence=CONFIDENCE):
    """Return signed-loss VaR and ES using the worst ceil(n × tail fraction) days."""
    values = np.asarray(returns, dtype=float)
    if values.ndim != 1 or not values.size or not np.isfinite(values).all():
        raise ValueError("Tail losses require a finite, nonempty return vector")
    if not 0 < confidence < 1:
        raise ValueError("Confidence must be between zero and one")
    tail_count = max(1, math.ceil(round(values.size * (1 - confidence), 12)))
    losses = np.sort(-values)[-tail_count:]
    return float(losses[0]), float(losses.mean())


def risk_snapshot(history, weights):
    """Estimate current-weight risks from an ordered frame of daily sleeve returns."""
    if set(history.columns) != set(SYMBOLS) or len(history.columns) != len(SYMBOLS):
        raise ValueError("History must contain SPY, IEF and GLD returns")
    if not history.index.is_monotonic_increasing or history.index.has_duplicates:
        raise ValueError("Return observations must be ordered and unique")
    if set(weights) != set(SYMBOLS):
        raise ValueError("Weights must be specified for every sleeve")
    exposure = np.asarray([weights[symbol] for symbol in SYMBOLS], dtype=float)
    if not np.isfinite(exposure).all() or (exposure < 0).any() or exposure.sum() > 1 + 1e-12:
        raise ValueError("Weights must be finite, long-only and unlevered")
    sample = history.loc[:, list(SYMBOLS)].tail(LOOKBACK).to_numpy(float)
    if len(sample) != LOOKBACK or not np.isfinite(sample).all():
        raise ValueError(f"Risk estimates require {LOOKBACK} complete daily returns")

    portfolio_returns = sample @ exposure
    var, expected_shortfall = expected_tail_loss(portfolio_returns)
    annual_covariance = np.cov(sample, rowvar=False) * SESSIONS_PER_YEAR
    covariance_times_weight = annual_covariance @ exposure
    volatility = math.sqrt(max(0.0, float(exposure @ covariance_times_weight)))
    contribution = (
        exposure * covariance_times_weight / volatility if volatility > 0 else np.zeros(len(SYMBOLS))
    )
    stress = {}
    for name, shocks in SCENARIOS.items():
        impact = {symbol: float(exposure[i]) * shocks[symbol] for i, symbol in enumerate(SYMBOLS)}
        stress[name] = {"portfolio_return": sum(impact.values()), "asset_contribution": impact}

    return {
        "observations": len(sample),
        "confidence": CONFIDENCE,
        "annualized_volatility": volatility,
        "var_95": var,
        "expected_shortfall_95": expected_shortfall,
        "cash_weight": max(0.0, 1 - float(exposure.sum())),
        "assets": {
            symbol: {"weight": float(exposure[i]), "volatility_contribution": float(contribution[i])}
            for i, symbol in enumerate(SYMBOLS)
        },
        "stress": stress,
    }


def risk_scale(history, lookback=LOOKBACK):
    """Scale strategic weights toward cash when trailing ES exceeds the loss budget."""
    recent = history.loc[:, list(SYMBOLS)].tail(lookback)
    if len(recent) != lookback:
        raise ValueError(f"Risk overlay requires {lookback} completed return days")
    _, es = expected_tail_loss(recent.to_numpy(float) @ np.array(list(BASE_WEIGHTS.values())))
    return DAILY_ES_LIMIT / es if es > DAILY_ES_LIMIT else 1.0


def adjusted_close_returns(prices):
    """Compute close-to-close total-return proxy changes without filling gaps."""
    return pd.DataFrame(prices).pct_change(fill_method=None).iloc[1:]
