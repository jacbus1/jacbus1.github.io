"""Tests for causal fills, self-financing costs, risk estimates, and data integrity."""

from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

import exchange_calendars as xcals
import numpy as np
import pandas as pd
from pandas.testing import assert_frame_equal

from engine.backtest import metrics, rebalance, run_policy
from engine.data import FIELDS, PRICE_END, PRICE_START, MarketData, load_prices
from engine.model import (
    BASE_WEIGHTS, LOOKBACK, SESSIONS_PER_YEAR, SYMBOLS, adjusted_close_returns, expected_tail_loss, risk_snapshot,
)


class BacktestTests(unittest.TestCase):
    def test_rebalance_stays_self_financing_and_charges_both_directions(self):
        old_values = np.array([0.7, 0.2, 0.0])
        weights = np.array([0.4, 0.4, 0.1])
        target, cash, fee, turnover = rebalance(old_values, 0.1, weights, 0.0005)
        nav = target.sum() + cash
        np.testing.assert_allclose(target / nav, weights)
        self.assertAlmostEqual(nav + fee, 1.0)
        self.assertAlmostEqual(turnover, np.abs(target - old_values).sum())
        self.assertAlmostEqual(fee, 0.0005 * turnover)
        self.assertGreater(cash, 0)

    def test_existing_exposure_bears_rebalance_day_overnight_gap(self):
        dates = pd.to_datetime(["2020-01-30", "2020-01-31", "2020-02-03", "2020-02-04"])
        closes = pd.DataFrame(100.0, index=dates, columns=SYMBOLS)
        closes.loc[dates[2]:, "SPY"] = 50.0
        market = MarketData(closes.copy(), closes, {})
        path, trades = run_policy(market, adjusted_close_returns(closes), dates[1], dates[-1], 0, constant_scale=1)
        self.assertAlmostEqual(path.at[dates[2], "daily_return"], -0.30)
        self.assertAlmostEqual(path.at[dates[2], "SPY"], 0.60)
        self.assertEqual(trades.at[dates[2], "signal_date"], dates[1])

    def test_future_prices_and_same_day_close_cannot_change_earlier_trades(self):
        dates = pd.bdate_range("2019-01-01", "2020-06-15")
        random = np.random.default_rng(1)
        closes = pd.DataFrame(100 * (1 + random.normal(0, 0.012, (len(dates), len(SYMBOLS)))).cumprod(axis=0),
                              index=dates, columns=SYMBOLS)
        opens = closes.shift(1).fillna(closes)
        original = MarketData(opens, closes, {})
        cutoff = pd.Timestamp("2020-03-02")
        short = MarketData(opens.loc[:cutoff], closes.loc[:cutoff], {})
        long_path, long_trades = run_policy(original, adjusted_close_returns(closes), "2020-01-02", dates[-1], 5)
        path, trades = run_policy(short, adjusted_close_returns(short.closes), "2020-01-02", cutoff, 5)
        assert_frame_equal(path, long_path.loc[:cutoff])
        assert_frame_equal(trades, long_trades.loc[:cutoff])

        stressed_closes = short.closes.copy()
        stressed_closes.at[cutoff, "SPY"] *= 0.5
        stressed = MarketData(short.opens, stressed_closes, {})
        stressed_path, stressed_trades = run_policy(
            stressed, adjusted_close_returns(stressed.closes), "2020-01-02", cutoff, 5,
        )
        assert_frame_equal(stressed_trades, trades)
        self.assertLess(stressed_path.at[cutoff, "nav"], path.at[cutoff, "nav"])

    def test_initial_fee_and_first_day_decline_count_in_drawdown(self):
        dates = pd.to_datetime(["2020-01-30", "2020-01-31", "2020-02-03"])
        closes = pd.DataFrame(100.0, index=dates, columns=SYMBOLS)
        closes.loc[dates[1], "SPY"] = 95.0
        opens = pd.DataFrame(100.0, index=dates, columns=SYMBOLS)
        path, trades = run_policy(MarketData(opens, closes, {}), adjusted_close_returns(closes), dates[1], dates[-1], 5,
                                  constant_scale=1)
        self.assertAlmostEqual(metrics(path, trades)["max_drawdown"], 0.97 / 1.0005 - 1)

    def test_yield_is_on_prior_held_cash_and_zero_is_the_baseline(self):
        dates = pd.to_datetime(["2020-01-30", "2020-01-31", "2020-02-03", "2020-02-04"])
        close = pd.DataFrame(100.0, index=dates, columns=SYMBOLS)
        market = MarketData(close.copy(), close, {})
        args = (market, adjusted_close_returns(close), dates[1], dates[-1], 0)
        base, base_trades = run_policy(*args, constant_scale=0.50)
        zero, zero_trades = run_policy(*args, constant_scale=0.50, annual_cash_yield=0)
        assert_frame_equal(base, zero, check_exact=True)
        assert_frame_equal(base_trades, zero_trades, check_exact=True)
        yielding, _ = run_policy(*args, constant_scale=0.50, annual_cash_yield=0.04)
        daily_rate = 1.04 ** (1 / SESSIONS_PER_YEAR) - 1
        self.assertAlmostEqual(yielding.at[dates[1], "nav"], 1.0)
        self.assertAlmostEqual(yielding.at[dates[2], "daily_return"], 0.5 * daily_rate)
        self.assertAlmostEqual(yielding.at[dates[3], "nav"], (1 + 0.5 * daily_rate) ** 2)


class RiskTests(unittest.TestCase):
    def test_var_and_es_use_worst_five_percent_rounded_up(self):
        values = [0.01] * 19 + [-0.03]
        self.assertEqual(expected_tail_loss(values), (0.03, 0.03))
        self.assertEqual(expected_tail_loss(values + [-0.05]), (0.03, 0.04))

    def test_stress_and_component_vol_use_actual_risky_weights(self):
        dates = pd.bdate_range("2020-01-01", periods=LOOKBACK)
        returns = pd.DataFrame(np.random.default_rng(7).normal(0, 0.01, (len(dates), len(SYMBOLS))),
                               index=dates, columns=SYMBOLS)
        full = risk_snapshot(returns, BASE_WEIGHTS)
        half = risk_snapshot(returns, {k: v / 2 for k, v in BASE_WEIGHTS.items()})
        self.assertAlmostEqual(half["cash_weight"], 0.5)
        for field in ("annualized_volatility", "var_95", "expected_shortfall_95"):
            self.assertAlmostEqual(half[field], full[field] / 2)
        self.assertAlmostEqual(sum(v["volatility_contribution"] for v in half["assets"].values()),
                               half["annualized_volatility"])
        self.assertAlmostEqual(half["stress"]["equity_selloff"]["portfolio_return"], -0.0635)


class DataTests(unittest.TestCase):
    def test_full_calendar_and_common_open_close_adjustment(self):
        with TemporaryDirectory() as temp:
            file = Path(temp) / "prices.csv"
            dates = xcals.get_calendar("XNYS", start=PRICE_START, end=PRICE_END).sessions
            frame = pd.DataFrame(((date.date(), symbol, 99, 100, 80) for date in dates for symbol in SYMBOLS),
                                 columns=FIELDS)
            frame.to_csv(file, index=False)
            market = load_prices(file)
            self.assertEqual(market.provenance["expected_sessions"], len(dates))
            self.assertAlmostEqual(market.opens.iloc[0]["SPY"], 79.2)
            self.assertAlmostEqual(market.closes.iloc[0]["SPY"], 80)
            missing_date = frame.date.eq(frame.iloc[10].date)
            frame.loc[~missing_date].to_csv(file, index=False)
            with self.assertRaisesRegex(ValueError, "expected exchange session"):
                load_prices(file)


if __name__ == "__main__":
    unittest.main()
