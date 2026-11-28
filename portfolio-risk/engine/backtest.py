"""Next-open monthly rebalancing, accounting, and fixed chronological holdout."""

import math

import numpy as np
import pandas as pd

from .model import (
    BASE_WEIGHTS, CONFIDENCE, DAILY_ES_LIMIT, LOOKBACK, SCENARIOS, SESSIONS_PER_YEAR,
    SYMBOLS, adjusted_close_returns, expected_tail_loss, risk_scale, risk_snapshot,
)


DEVELOPMENT_START = "2007-01-03"
DEVELOPMENT_END = "2016-12-30"
HOLDOUT_START = "2017-01-03"
BASE_FEE_BPS = 5
FEE_SENSITIVITIES = (0, BASE_FEE_BPS, 10, 20)
LOOKBACK_SENSITIVITIES = (20, 60, 120, LOOKBACK)
CASH_YIELD_SENSITIVITIES_PCT = (0, 2, 4)
HISTORICAL_WINDOWS = {
    "global_financial_crisis": ("development", "2008-09-15", "2009-03-09"),
    "pandemic_selloff": ("holdout", "2020-02-20", "2020-03-23"),
    "pandemic_rebound_through_q2": ("holdout", "2020-03-24", "2020-06-30"),
    "inflation_and_rates_2022": ("holdout", "2022-01-03", "2022-10-12"),
}


def rebalance(asset_values, cash, target_weights, fee_rate):
    """Set post-cost weights and charge a fee on total absolute traded notional."""
    pretrade_nav = float(asset_values.sum() + cash)
    lower, upper = 0.0, pretrade_nav
    for _ in range(52):
        candidate = (lower + upper) / 2
        volume = np.abs(candidate * target_weights - asset_values).sum()
        if candidate + fee_rate * volume <= pretrade_nav:
            lower = candidate
        else:
            upper = candidate
    target = lower * target_weights
    turnover = float(np.abs(target - asset_values).sum())
    fee = fee_rate * turnover
    # Round-off at full investment can put residual cash slightly below zero.
    new_cash = max(0.0, pretrade_nav - target.sum() - fee)
    return target, float(new_cash), fee, turnover


def run_policy(market, close_returns, start, end, fee_bps, *, constant_scale=None,
               lookback=LOOKBACK, annual_cash_yield=0.0):
    """Run a standalone cash-funded period using completed close observations."""
    dates = market.closes.index
    opens, closes = market.opens.to_numpy(), market.closes.to_numpy()
    first, past_last = dates.slice_locs(start, end)
    if first == past_last or first < 1:
        raise ValueError("The replay period must follow at least one prior price session")
    if not 0 <= fee_bps < 10000:
        raise ValueError("Fee basis points must be within [0, 10000)")
    if constant_scale is not None and not 0 <= constant_scale <= 1:
        raise ValueError("Constant scale must be within [0, 1]")
    if not math.isfinite(annual_cash_yield) or annual_cash_yield <= -1:
        raise ValueError("Annual cash yield must be finite and greater than -100%")
    cash_growth = (1 + annual_cash_yield) ** (1 / SESSIONS_PER_YEAR)
    strategic = np.fromiter(BASE_WEIGHTS.values(), dtype=float)
    previous_nav = cash = 1.0
    shares = np.zeros(len(SYMBOLS))
    records = []
    trades = []
    for i in range(first, past_last):
        session = dates[i]
        if i > first:
            cash *= cash_growth
        if i == first or session.month != dates[i - 1].month:
            signal_date = dates[i - 1]
            scale = constant_scale
            if scale is None:
                scale = risk_scale(close_returns.loc[:signal_date].tail(lookback), lookback=lookback)
            pretrade_nav = float(shares @ opens[i] + cash)
            asset_values, cash, fee, turnover = rebalance(
                shares * opens[i], cash, scale * strategic, fee_bps / 10000,
            )
            shares = asset_values / opens[i]
            trades.append({
                "signal_date": signal_date, "execution_date": session, "scale": scale,
                "fee": fee, "turnover": turnover / pretrade_nav,
            })
        closing_values = shares * closes[i]
        nav = float(closing_values.sum() + cash)
        records.append({
            "date": session,
            "nav": nav,
            "daily_return": nav / previous_nav - 1,
            "cash": cash / nav,
            **dict(zip(SYMBOLS, closing_values / nav)),
        })
        previous_nav = nav
    return pd.DataFrame(records).set_index("date"), pd.DataFrame(trades).set_index("execution_date")


def drawdowns(nav, initial_nav):
    peak = nav.cummax().clip(lower=initial_nav)
    return nav / peak - 1


def metrics(path, trades):
    returns = path.daily_return
    ending_nav = float(path.nav.iloc[-1])
    annualized_volatility = float(returns.std(ddof=1) * math.sqrt(SESSIONS_PER_YEAR))
    var, es = expected_tail_loss(returns)
    return {
        "sessions": len(path),
        "total_return": ending_nav - 1,
        "cagr": ending_nav ** (SESSIONS_PER_YEAR / len(path)) - 1,
        "annualized_volatility": annualized_volatility,
        "sharpe_zero_rate": (
            float(returns.mean()) * SESSIONS_PER_YEAR / annualized_volatility
            if annualized_volatility > 0 else None
        ),
        "max_drawdown": float(drawdowns(path.nav, 1.0).min()),
        "daily_var_95": var,
        "daily_expected_shortfall_95": es,
        "worst_daily_return": float(returns.min()),
        "average_cash_weight": float(path.cash.mean()),
        "average_close_weights": {symbol: float(path[symbol].mean()) for symbol in SYMBOLS},
        "annualized_gross_turnover": float(trades.turnover.sum()) * SESSIONS_PER_YEAR / len(path),
        "total_fees_as_fraction_of_starting_nav": float(trades.fee.sum()),
        "rebalances": len(trades),
    }


def historical_summary(paths, close_returns):
    results = {}
    for name, (period, start, end) in HISTORICAL_WINDOWS.items():
        policies = {}
        for policy, (path, _) in paths[period].items():
            window = path.loc[start:end]
            opening_nav = float(window.nav.iloc[0] / (1 + window.daily_return.iloc[0]))
            policies[policy] = {
                "return": float(window.nav.iloc[-1]) / opening_nav - 1,
                "max_drawdown": float(drawdowns(window.nav, opening_nav).min()),
                "average_cash_weight": float(window.cash.mean()),
            }
        results[name] = {
            "period": period,
            "first_session": start,
            "last_session": end,
            "spy_ief_daily_return_correlation": float(close_returns.loc[start:end, ["SPY", "IEF"]].corr().iloc[0, 1]),
            "strategies": policies,
        }
    return results


def month_end_curve(paths):
    """A derived aggregate series indexed to the holdout's initial account value."""
    monthly = {}
    for policy, (path, _) in paths.items():
        summary = pd.DataFrame({"nav": path.nav, "drawdown": drawdowns(path.nav, 1.0)})
        for stamp, row in summary.groupby(summary.index.to_period("M")).tail(1).iterrows():
            date = stamp.date().isoformat()
            monthly.setdefault(date, {})[policy] = {
                "nav": round(float(row.nav), 8), "drawdown": round(float(row.drawdown), 8),
            }
    return [{"date": date, **values} for date, values in monthly.items()]


def build_report(market):
    last = market.closes.index[-1].date().isoformat()
    returns = adjusted_close_returns(market.closes)
    training = (market, returns, DEVELOPMENT_START, DEVELOPMENT_END, BASE_FEE_BPS)
    development = {
        "fixed_weights": run_policy(*training, constant_scale=1.0),
        "es_overlay": run_policy(*training),
    }
    overlay_training, _ = development["es_overlay"]
    matched_scale = float(1 - overlay_training.cash.mean())
    development["matched_static_cash"] = run_policy(*training, constant_scale=matched_scale)
    scales = {"fixed_weights": 1.0, "es_overlay": None, "matched_static_cash": matched_scale}
    holdout = {}
    sensitivity = {}
    for fee in FEE_SENSITIVITIES:
        fee_paths = {
            key: run_policy(market, returns, HOLDOUT_START, last, fee, constant_scale=scale)
            for key, scale in scales.items()
        }
        sensitivity[str(fee)] = {policy: metrics(*path) for policy, path in fee_paths.items()}
        if fee == BASE_FEE_BPS:
            holdout = fee_paths

    latest = {
        policy: risk_snapshot(returns.tail(LOOKBACK), path.iloc[-1][list(SYMBOLS)].to_dict())
        for policy, (path, _) in holdout.items()
    }
    lookback_sensitivity = {str(LOOKBACK): sensitivity[str(BASE_FEE_BPS)]["es_overlay"]}
    for observations in LOOKBACK_SENSITIVITIES:
        if observations != LOOKBACK:
            lookback_sensitivity[str(observations)] = metrics(*run_policy(
                market, returns, HOLDOUT_START, last, BASE_FEE_BPS, lookback=observations,
            ))
    cash_yield_sensitivity = {"0": sensitivity[str(BASE_FEE_BPS)]}
    for yield_pct in CASH_YIELD_SENSITIVITIES_PCT[1:]:
        cash_yield_sensitivity[str(yield_pct)] = {
            key: metrics(*run_policy(market, returns, HOLDOUT_START, last, BASE_FEE_BPS,
                                     constant_scale=scale, annual_cash_yield=yield_pct / 100))
            for key, scale in scales.items()
        }
    return {
        "schema_version": 1,
        "provenance": market.provenance,
        "methodology": {
            "strategic_weights": BASE_WEIGHTS,
            "observations_per_year": SESSIONS_PER_YEAR,
            "cash_annual_yield": 0.0,
            "initial_nav_each_period": 1.0,
            "transaction_fee_bps_per_absolute_traded_dollar": BASE_FEE_BPS,
            "execution_rule": "First exchange session of month at open, signal from prior completed close",
            "risk_lookback_sessions": LOOKBACK,
            "risk_confidence": CONFIDENCE,
            "risk_daily_es_limit": DAILY_ES_LIMIT,
            "matched_static_invested_fraction_from_development": matched_scale,
            "assumed_shocks": SCENARIOS,
        },
        "development": {
            "first_session": DEVELOPMENT_START, "last_session": DEVELOPMENT_END,
            "strategies": {policy: metrics(*path) for policy, path in development.items()},
        },
        "holdout": {
            "first_session": HOLDOUT_START, "last_session": last,
            "strategies": sensitivity[str(BASE_FEE_BPS)],
        },
        "holdout_fee_sensitivity_bps": sensitivity,
        "holdout_es_lookback_sensitivity_sessions": lookback_sensitivity,
        "holdout_cash_yield_sensitivity_pct": cash_yield_sensitivity,
        "historical_stress": historical_summary({"development": development, "holdout": holdout}, returns),
        "latest_risk": {"as_of_session": last, "strategies": latest},
        "holdout_monthly_curve": month_end_curve(holdout),
    }
