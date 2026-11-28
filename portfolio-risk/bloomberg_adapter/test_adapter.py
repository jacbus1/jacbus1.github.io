from dataclasses import replace
from datetime import date, timedelta
import hashlib
from pathlib import Path
import unittest

from bloomberg_adapter import (
    REFDATA_SERVICE,
    BloombergAdapter,
    Event,
    FixtureTransport,
    Request,
    RequestError,
)


ALPHA = "DEMO_ALPHA US Equity"
MAPLE = "DEMO_MAPLE CN Equity"
UNKNOWN = "UNKNOWN US Equity"
SNAPSHOT = date(2025, 6, 30)


class ScriptedTransport:
    def __init__(self, script):
        self.source = FixtureTransport().source
        self.script = script

    def events(self, request, correlation_id):
        self.request = request
        return self.script(request, correlation_id)


def reference_message(*, price=42, day="2025-06-30"):
    return {"securityData": [{
        "security": ALPHA,
        "fieldData": {"PX_LAST": price},
        "fieldAsOf": {"PX_LAST": day} if day else {},
    }]}


class AdapterTests(unittest.TestCase):
    def setUp(self):
        self.transport = FixtureTransport()
        self.client = BloombergAdapter(self.transport)

    def test_reference_keeps_values_dates_and_provenance_after_multiple_events(self):
        result = self.client.reference([MAPLE, ALPHA], ["PX_LAST", "CRNCY"], as_of=SNAPSHOT)
        values = {(item.security, item.field): item.value for item in result.observations}
        self.assertEqual(values[(ALPHA, "CRNCY")], "USD")
        self.assertEqual(values[(MAPLE, "CRNCY")], "CAD")
        self.assertGreater(values[(MAPLE, "PX_LAST")], 0)
        self.assertEqual(len(result.observations), 4)
        self.assertEqual(result.issues, ())
        self.assertEqual(result.provenance.service, REFDATA_SERVICE)
        self.assertEqual(result.provenance.request_type, "ReferenceDataRequest")
        self.assertEqual(result.provenance.source.mode, "synthetic")
        fixture = Path(__file__).with_name("market_data.json").read_bytes()
        self.assertEqual(result.provenance.source.revision, hashlib.sha256(fixture).hexdigest())
        self.assertTrue(all(item.as_of == SNAPSHOT for item in result.observations))
        self.assertTrue(all(item.provenance == result.provenance for item in result.observations))

    def test_history_uses_inclusive_dates_and_the_same_close_as_reference(self):
        result = self.client.historical([ALPHA], ["PX_LAST"], start=date(2025, 6, 26), end=SNAPSHOT)
        self.assertEqual([item.as_of.day for item in result.observations], [26, 27, 30])
        self.assertEqual(result.provenance.request_type, "HistoricalDataRequest")
        self.assertEqual(result.issues, ())
        mark = self.client.reference([ALPHA], ["PX_LAST"], as_of=SNAPSHOT).observations[0]
        self.assertEqual(result.observations[-1].value, mark.value)
        self.assertNotEqual(result.provenance.correlation_id, mark.provenance.correlation_id)

    def test_request_parameter_mapping(self):
        transport = ScriptedTransport(self.transport.events)
        client = BloombergAdapter(transport)
        client.historical([ALPHA], ["PX_LAST"], start=date(2025, 6, 26), end=SNAPSHOT)
        self.assertEqual(transport.request.parameters(), {
            "securities": [ALPHA], "fields": ["PX_LAST"],
            "startDate": "20250626", "endDate": "20250630", "periodicitySelection": "DAILY",
        })
        client.reference([ALPHA, ALPHA], ["PX_LAST", " PX_LAST"], as_of=SNAPSHOT)
        self.assertEqual(transport.request.parameters(), {"securities": [ALPHA], "fields": ["PX_LAST"]})

    def test_security_and_field_errors_preserve_available_fields(self):
        result = self.client.reference([ALPHA, UNKNOWN], ["PX_LAST", "UNKNOWN_FIELD"], as_of=SNAPSHOT)
        self.assertEqual([(item.security, item.field) for item in result.observations], [(ALPHA, "PX_LAST")])
        self.assertEqual({(issue.scope, issue.code, issue.security, issue.field) for issue in result.issues}, {
            ("field", "BAD_FLD", ALPHA, "UNKNOWN_FIELD"), ("security", "BAD_SEC", UNKNOWN, None),
        })

    def test_history_preserves_field_error_for_reference_only_mnemonic(self):
        result = self.client.historical([ALPHA], ["PX_LAST", "CRNCY"], start=SNAPSHOT, end=SNAPSHOT)
        self.assertEqual([item.field for item in result.observations], ["PX_LAST"])
        self.assertEqual([(issue.field, issue.code) for issue in result.issues], [("CRNCY", "BAD_FLD")])

    def test_empty_history_is_reported_as_no_data(self):
        result = self.client.historical([ALPHA], ["PX_LAST"], start=date(2025, 6, 28), end=date(2025, 6, 29))
        self.assertEqual(result.observations, ())
        self.assertEqual(result.issues[0].code, "NO_DATA")

    def test_snapshot_rejects_stale_and_future_values_without_relabelling(self):
        next_day = SNAPSHOT + timedelta(days=1)
        previous_day = SNAPSHOT - timedelta(days=1)
        result = self.client.reference([ALPHA], ["PX_LAST"], as_of=next_day)
        self.assertEqual(result.observations, ())
        self.assertEqual((result.issues[0].code, result.issues[0].as_of), ("STALE_VALUE", SNAPSHOT))
        result = self.client.reference([ALPHA], ["PX_LAST"], as_of=next_day, max_age_days=1)
        self.assertEqual(result.observations[0].as_of, SNAPSHOT)
        result = self.client.reference([ALPHA], ["PX_LAST"], as_of=previous_day, max_age_days=1)
        self.assertEqual(result.observations, ())
        self.assertEqual((result.issues[0].code, result.issues[0].as_of), ("FUTURE_VALUE", SNAPSHOT))

    def test_undated_value_is_rejected(self):
        def script(_, correlation_id):
            yield Event("RESPONSE", correlation_id, (reference_message(day=None),))

        result = BloombergAdapter(ScriptedTransport(script)).reference([ALPHA], ["PX_LAST"], as_of=SNAPSHOT)
        self.assertEqual(result.observations, ())
        self.assertEqual(result.issues[0].code, "UNDATED_VALUE")

    def test_date_range_is_enforced_when_transport_returns_extra_history(self):
        def script(_, correlation_id):
            yield Event("RESPONSE", correlation_id, ({"securityData": {
                "security": ALPHA,
                "fieldData": [{"date": "2025-06-27", "PX_LAST": 41}, {"date": "2025-06-30", "PX_LAST": 42}],
            }},))

        result = BloombergAdapter(ScriptedTransport(script)).historical(
            [ALPHA], ["PX_LAST"], start=SNAPSHOT, end=SNAPSHOT,
        )
        self.assertEqual([item.value for item in result.observations], [42])
        self.assertEqual(result.issues[0].code, "OUTSIDE_DATE_RANGE")

    def test_unrelated_response_does_not_complete_request(self):
        def script(_, correlation_id):
            yield Event("RESPONSE", "unrelated", (reference_message(price=98),))
            yield Event("PARTIAL_RESPONSE", correlation_id, (reference_message(price=42),))
            yield Event("RESPONSE", correlation_id)

        result = BloombergAdapter(ScriptedTransport(script)).reference([ALPHA], ["PX_LAST"], as_of=SNAPSHOT)
        self.assertEqual([item.value for item in result.observations], [42])

    def assert_reference_error(self, script, code):
        with self.assertRaises(RequestError) as caught:
            BloombergAdapter(ScriptedTransport(script)).reference([ALPHA], ["PX_LAST"], as_of=SNAPSHOT)
        self.assertEqual(caught.exception.issue.code, code)
        self.assertEqual(caught.exception.issue.scope, "request")
        self.assertEqual(caught.exception.provenance.request_type, "ReferenceDataRequest")

    def test_unknown_service_reports_endpoint_failure(self):
        def script(request, correlation_id):
            return self.transport.events(replace(request, service="//blp/unknown"), correlation_id)

        self.assert_reference_error(script, "SERVICE_UNAVAILABLE")
        request = Request(REFDATA_SERVICE, "UnknownRequest", (ALPHA,), ("PX_LAST",))
        result = tuple(self.transport.events(request, "sample-id"))
        self.assertEqual(result[0].kind, "REQUEST_STATUS")
        self.assertEqual(result[0].messages[0]["reason"]["category"], "BAD_REQUEST")

    def test_request_fails_if_event_stream_ends_after_partial(self):
        def script(_, correlation_id):
            yield Event("PARTIAL_RESPONSE", correlation_id, (reference_message(),))

        self.assert_reference_error(script, "INCOMPLETE_RESPONSE")

    def test_request_timeout_discards_partial_data(self):
        def script(_, correlation_id):
            yield Event("PARTIAL_RESPONSE", correlation_id, (reference_message(),))
            yield Event("TIMEOUT", correlation_id)

        self.assert_reference_error(script, "TIMEOUT")

    def test_request_response_error_discards_partial_data(self):
        def script(_, correlation_id):
            yield Event("PARTIAL_RESPONSE", correlation_id, (reference_message(),))
            yield Event("RESPONSE", correlation_id, ({"responseError": {
                "category": "NOT_AUTHORIZED", "message": "Simulated entitlement failure",
            }},))

        self.assert_reference_error(script, "NOT_AUTHORIZED")

    def test_conflicting_partial_values_fail_instead_of_overwriting(self):
        def script(_, correlation_id):
            yield Event("PARTIAL_RESPONSE", correlation_id, (reference_message(price=42),))
            yield Event("RESPONSE", correlation_id, (reference_message(price=43),))

        self.assert_reference_error(script, "CONFLICTING_OBSERVATION")

    def test_malformed_response_is_reported_at_request_level(self):
        def script(_, correlation_id):
            yield Event("RESPONSE", correlation_id, (reference_message(price=float("nan")),))

        self.assert_reference_error(script, "MALFORMED_RESPONSE")

    def test_invalid_requests_are_rejected_before_sending(self):
        with self.assertRaises(ValueError):
            self.client.reference(ALPHA, ["PX_LAST"], as_of=SNAPSHOT)
        with self.assertRaises(ValueError):
            self.client.reference([ALPHA], [], as_of=SNAPSHOT)
        with self.assertRaises(ValueError):
            self.client.reference([ALPHA], ["PX_LAST"], as_of=SNAPSHOT, max_age_days=-1)
        with self.assertRaises(ValueError):
            self.client.historical([ALPHA], ["PX_LAST"], start=SNAPSHOT, end=date(2025, 6, 26))


if __name__ == "__main__":
    unittest.main()
