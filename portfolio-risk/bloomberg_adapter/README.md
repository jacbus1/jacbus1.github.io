# Bloomberg-style market data adapter

The portfolio risk demo uses a Python standard-library adapter and a synthetic fixture. It runs offline with Python 3.10 or newer. The fixture models six equities and a CAD-per-USD rate over 128 simulated weekdays from January 2 through June 30, 2025. These dates form a weekday grid and include exchange holidays. All identifiers and observations are fictional.

## Run the example

Run this from the directory containing `bloomberg_adapter`.

```python
from datetime import date

from bloomberg_adapter import BloombergAdapter, FixtureTransport

client = BloombergAdapter(FixtureTransport())
securities = ["DEMO_ALPHA US Equity", "DEMO_MAPLE CN Equity"]
valuation_date = date(2025, 6, 30)

reference = client.reference(securities, ["PX_LAST", "CRNCY"], as_of=valuation_date)
history = client.historical(
    securities, ["PX_LAST"], start=date(2025, 6, 26), end=valuation_date,
)

for result in (reference, history):
    print(result.provenance.request_type, result.provenance.source.mode)
    for observation in result.observations:
        print(observation.as_of, observation.security, observation.field, observation.value)
    for issue in result.issues:
        print(issue.scope, issue.security, issue.field, issue.code, issue.message)
```

`PX_LAST`, `CRNCY`, and `NAME` are available on reference requests. The historical fixture supplies daily `PX_LAST`. `DEMO_USDCAD Curncy` is quoted in CAD per USD, so a CAD-denominated price divided by the same-day FX value gives the price in USD. Each observation carries its original date, the service and operation, its request correlation ID, and the fixture name and SHA-256 revision.

## Response handling

- The adapter collects matching `PARTIAL_RESPONSE` events through the terminal `RESPONSE`. A request-level failure, timeout, missing final event, or conflicting duplicate raises `RequestError` and withholds all observations from that request. The error carries an `issue` and request `provenance`.
- Security errors and field exceptions appear in `Result.issues` alongside available observations. A missing series or reference field produces `NO_DATA`. The caller can check the coverage required by its risk calculation, such as matching equity and FX dates.
- A reference request applies the valuation date and `max_age_days` to returned observations. The default age limit is zero calendar days. Values from after the valuation date and values without a supplied observation date are excluded. To retrieve prices for an earlier date, use `historical()` with an inclusive date range.

```sh
python3 -B -m unittest bloomberg_adapter.test_adapter -v
```

## Integrating a licensed Bloomberg session

`Transport.events(request, correlation_id)` is the integration seam. The demo ships with `FixtureTransport`, which creates normalized response events locally. An SDK integration can implement the same protocol and pass its transport to `BloombergAdapter`.

1. Use an authorized Bloomberg session, open `request.service` (`//blp/refdata`), and create `request.operation` on that service. Populate the request from `request.parameters()`, which supplies the security and field arrays and, for historical requests, the `YYYYMMDD` bounds and `DAILY` periodicity. [Bloomberg Core Developer Guide, section 13](https://data.bloomberglp.com/professional/sites/4/BLPAPI-Core-Developer-Guide.pdf)
2. Send the request with a correlation ID and use a request-specific event queue with a bounded wait. Map the SDK correlation ID back to the adapter's supplied ID. Yield normalized `Event` objects through the final response. Surface SDK request failures as `REQUEST_STATUS` with a `reason` mapping and elapsed deadlines as `TIMEOUT` with the matching correlation ID. [Bloomberg Python SDK session documentation](https://bloomberg.github.io/blpapi-docs/python/3.26.10/_autosummary/blpapi.Session.html#blpapi.Session.sendRequest)
3. Normalize each message to a mapping. Reference messages use a `securityData` list whose entries contain `security` and a scalar-valued `fieldData` mapping. A historical message contains one `securityData` mapping, with `fieldData` holding rows whose `date` values are ISO calendar dates. Normalize security identifiers back to the requested identifier if needed. Pass through `responseError`, `securityError`, and `fieldExceptions` under the same keys used by the Bloomberg response schema. [Bloomberg response payload examples](https://github.com/bloomberg/blpapi-http/blob/develop/doc/http-api-guide.md#reference-data-service-blprefdata)
4. Supply `fieldAsOf` for reference values as a mapping from field to a verified ISO observation date. The adapter adds this metadata requirement for the portfolio's freshness check. Leave a field's date absent when the transport cannot establish the observation date. Use the transport's `Source` object to identify the approved feed and its revision or snapshot identifier.
