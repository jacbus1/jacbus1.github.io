# ETF portfolio risk replay

The simulation compares three USD portfolios built from a hypothetical 60% U.S. equity, 30% intermediate U.S. Treasury and 10% gold allocation. The series represent SPY, IEF and GLD. Each portfolio uses the same rebalance calendar and trading-cost model. [SPY issuer](https://www.ssga.com/us/en/intermediary/etfs/state-street-spdr-sp-500-etf-trust-spy) · [IEF issuer](https://www.ishares.com/us/literature/fact-sheet/ief-ishares-7-10-year-treasury-bond-etf-fund-fact-sheet-en-us.pdf) · [GLD issuer](https://www.spdrgoldshares.com/usa/)

| Policy | Target at each monthly rebalance |
| --- | --- |
| Fixed weights | Invest 100% at the 60/30/10 strategic weights. |
| Expected shortfall overlay | Compute the strategic basket's trailing 126-session historical daily 95% expected shortfall. When it exceeds 1%, multiply each asset weight by `0.01 / ES` and hold the rest in cash. |
| Matched static cash | Invest at the strategic proportions with a constant cash target equal to the overlay's average closing cash weight in the 2007–2016 development period. |

Each rebalance uses returns known through the previous session's close and executes at the first open of the month. Existing holdings bear overnight moves through the next open. The model sizes fractional units against that opening print and solves for exact post-cost target weights. It charges 5 basis points per dollar bought or sold and holds idle USD cash at 0% yield. Cost sensitivity applies 0, 5, 10 and 20 basis points to each policy. The final close values existing positions. The model assumes opening fills and excludes market impact, taxes and terminal liquidation.

The cash sensitivity applies hypothetical constant annual yields of 0%, 2% and 4% to each portfolio at the 5-basis-point fee. The model compounds the cash held at the previous close once per session before the next open. The initial session earns no interest. The 252-session rate convention leaves market-price returns, targets and the development-fitted cash allocation unchanged.

The development period runs from January 3, 2007 through December 30, 2016. The historical holdout runs from January 3, 2017 through September 15, 2026. Each starts from 1.0 NAV in cash, with earlier returns available for signal warm-up. The main overlay's budget and lookback are fixed across both periods. The alternate 20-, 60- and 120-session lookbacks are diagnostics and the 126-session specification remains the primary comparison. The holdout is retrospective and the basket consists of funds selected with hindsight.

Yahoo Finance daily histories cover all 5,207 XNYS exchange sessions from January 3, 2006 through September 15, 2026 for all three funds. The model uses each provider adjusted close and converts the daily open to the same basis with `open × adjusted_close / close`. This is a total-return proxy with provider dividend and split adjustments. The optional archive check verified the split-adjusted open and close for all 5,207 SPY sessions against the archived Yahoo data within floating-point precision. The JSON records the local normalized CSV hash for a reproducible run against the same input. Provider revisions can change a later download. [Yahoo adjusted close](https://help.yahoo.com/kb/SLN28256.html) · [yfinance price history](https://ranaroussi.github.io/yfinance/reference/yfinance.price_history.html)

## Run

From the repository root, using Python 3.11 or newer with compatible package wheels:

```sh
python -m pip install -r engine/requirements.txt
python -m engine --fetch /tmp/etf-prices.csv --output /tmp/backtest.json
python -m engine --prices /tmp/etf-prices.csv --output /tmp/replayed-backtest.json
python -m unittest discover -s engine/tests -v
```

The input CSV columns are `date,symbol,open,close,adjusted_close`. A downloader receipt is saved beside it. The optional `--archive-spy-manifest` check requires `pyarrow` and access to the local archive referenced in the manifest. The public download command reproduces the market replay without that local comparison. The report contains aggregate portfolio outcomes, exposure summaries and monthly strategy NAV. Raw prices stay in the local CSV.

## Read the report

Metrics use each strategy's close-to-close net NAV changes, including the day of the initial entry. Volatility uses a sample standard deviation annualized with 252 sessions. CAGR uses the same 252-session convention. Drawdown measures the decline from the prior running NAV peak, including the starting value. Gross turnover sums absolute buy and sell notional divided by pretrade NAV at each rebalance. Report percentages are stored as fractions.

At 95% confidence, historical VaR is the smallest loss among the worst `ceil(0.05 × n)` daily observations. Expected shortfall averages those losses. The 126-observation risk window therefore uses seven tail observations. Realized holdout VaR and expected shortfall use realized strategy returns. The latest risk snapshot applies the actual final closing weights to each historical sleeve return vector. It reports component contributions to annualized volatility and deterministic scenario results from the sleeve shock times its weight. The probability and size of future losses depend on conditions outside these historical samples. A monthly risk target can be exceeded between rebalances and after abrupt price moves.
