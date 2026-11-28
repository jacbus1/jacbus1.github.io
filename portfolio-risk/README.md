# Portfolio Risk Management

Jacky Huang · Independent engineering project

A transparent portfolio-risk replay compares a fixed 60% SPY, 30% IEF and 10% GLD allocation with a monthly Expected Shortfall (ES) overlay and a static cash diagnostic calibrated on the development period. The public case study shows derived results from a historical replay of a hypothetical portfolio. Its separate Bloomberg sandbox uses synthetic observations.

## Results

The 2017-01-03 to 2026-09-15 holdout contains 2,438 trading sessions. Figures below are net of 5 basis points per absolute dollar traded and assume 0% idle cash yield.

| Holdout measure | Fixed allocation | ES overlay | Static cash diagnostic |
| --- | ---: | ---: | ---: |
| Annualized return | 11.05% | 7.05% | 8.78% |
| Annualized volatility | 11.10% | 8.04% | 8.78% |
| Maximum drawdown | −20.49% | −16.20% | −16.51% |
| Daily historical 95% ES | 1.66% | 1.24% | 1.32% |
| Average cash weight | 0.00% | 22.91% | 20.54% |

The overlay reduced drawdown by 4.29 percentage points versus fixed allocation and gave up 4.00 percentage points of annualized return. Realized daily 95% ES exceeded the 1.00% budget used to size monthly exposure. Compared with the static cash diagnostic, the overlay's drawdown improvement was 0.31 percentage points and it held more cash on average. During the 2020 rebound through June, the overlay gained 8.77%, compared with 18.23% for static cash and 23.56% for fixed allocation. At a hypothetical 4% idle cash yield, static cash still had a higher annualized return, at 9.65% versus 8.01%, and a slightly smaller maximum drawdown. One retrospective ETF sample establishes the historical trade-off for these assumptions. [Full aggregate results](results/backtest.json) · [Chronology and risk calculation](engine/README.md)

## Bloomberg simulation

The synthetic adapter models `//blp/refdata` reference and historical requests, partial responses, stale values, field and security errors and a request-level entitlement failure. In the historical case, 128 aligned mock date rows run through CAD-to-USD conversion and the same tail-loss function used in the risk project. The mock uses a weekday grid. A real setup would use the relevant exchange calendar, approved entitlements and a transport that normalizes licensed Bloomberg SDK responses. [Adapter and live integration seam](bloomberg_adapter/README.md) · [Bloomberg API Library](https://professional.bloomberg.com/support/api-library/)

## Model review

Claude Haiku 5.5, Qwen3 4B and IBM Granite 3.3 2B independently reviewed aggregate results. Claude ran via a separate Claude Code session, and both open-weight models ran locally with Ollama. The models surfaced caveats and also made interpretation errors. Their role was QA. The portfolio simulation and numerical results came from deterministic Python.

A separate eight-question checklist tests whether each model reads this run correctly. Claude scored 8/8, Qwen scored 3/8 and Granite scored 6/8. This small checklist covers only this one report. Its scores do not measure trading or forecasting performance. [Checked model responses](results/model-checklist.json) · [Reviewed findings](results/review.json)

## Reproduce

From the directory containing this README, in a Python 3.11+ environment with compatible package wheels:

```sh
python -m pip install -r engine/requirements.txt
python -m engine --fetch /tmp/portfolio-risk-prices.csv --output /tmp/portfolio-risk-replay.json
python -m engine --prices /tmp/portfolio-risk-prices.csv --output /tmp/portfolio-risk-replay-repeat.json
python -m unittest discover -s engine/tests -v
python -m unittest bloomberg_adapter.test_adapter -v
python make_bloomberg_demo.py
```

The historical download is subject to vendor revisions. Input receipts and hashes identify the replay. Public files include portfolio-level results and synthetic fixtures.

For independent review runs, use a signed-in Claude Code session and a local Ollama service with the two specified models installed:

```sh
ollama pull qwen3:4b
ollama pull granite3.3:2b
python run_reviews.py --output /tmp/portfolio-risk-review.json
python check_model_claims.py --output /tmp/portfolio-risk-model-checklist.json
```

Both scripts transmit only the listed replay aggregates to the model processes. The Claude model ID and command can be passed with `--claude-model` and `--claude-command`. A reviewer checks any narrative findings before publication. The GitHub Pages demo reads precomputed artifacts in the browser.
