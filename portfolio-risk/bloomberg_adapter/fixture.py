"""Replay wholly synthetic market data using the normalized event contract."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import date
import hashlib
import json
from pathlib import Path

from .adapter import Event, HISTORICAL_REQUEST, REFDATA_SERVICE, REFERENCE_REQUEST, Request, Source


class FixtureTransport:
    def __init__(self, path: str | Path | None = None):
        fixture = Path(path) if path is not None else Path(__file__).with_name("market_data.json")
        raw = fixture.read_bytes()
        data = json.loads(raw)
        self.source = Source(
            "Bloomberg-style fixture", "synthetic", data["dataset"], hashlib.sha256(raw).hexdigest(),
        )
        self.snapshot_date = date.fromisoformat(data["snapshotDate"])
        self._dates = tuple(date.fromisoformat(day) for day in data["dates"])
        self._securities = data["securities"]
        if tuple(sorted(set(self._dates))) != self._dates or self.snapshot_date not in self._dates:
            raise ValueError("Fixture dates must be unique, ordered, and include the snapshot date")
        if any(len(record["closes"]) != len(self._dates) for record in self._securities.values()):
            raise ValueError("Each fixture history needs a close position for each fixture date")
        self._snapshot_index = self._dates.index(self.snapshot_date)

    def events(self, request: Request, correlation_id: str) -> Iterator[Event]:
        if request.service != REFDATA_SERVICE:
            yield Event("REQUEST_STATUS", correlation_id, ({"reason": {
                "category": "SERVICE_UNAVAILABLE", "message": f"Unknown service {request.service}",
            }},))
            return
        if request.operation not in {REFERENCE_REQUEST, HISTORICAL_REQUEST}:
            yield Event("REQUEST_STATUS", correlation_id, ({"reason": {
                "category": "BAD_REQUEST", "message": f"Unknown operation {request.operation}",
            }},))
            return
        if not request.securities or not request.fields:
            yield Event("RESPONSE", correlation_id, ({"responseError": {
                "category": "BAD_REQUEST", "message": "Provide at least one security and field",
            }},))
            return

        historical = request.operation == HISTORICAL_REQUEST
        if historical and (request.start is None or request.end is None or request.start > request.end):
            yield Event("RESPONSE", correlation_id, ({"responseError": {
                "category": "BAD_REQUEST", "message": "Provide a valid historical date range",
            }},))
            return
        for index, security in enumerate(request.securities):
            block = self._security_data(request, security, historical)
            block["sequenceNumber"] = index
            kind = "RESPONSE" if index == len(request.securities) - 1 else "PARTIAL_RESPONSE"
            yield Event(kind, correlation_id, ({"securityData": block if historical else [block]},))

    def _security_data(self, request: Request, security: str, historical: bool) -> dict:
        block = {"security": security}
        record = self._securities.get(security)
        if record is None:
            block["securityError"] = {"category": "BAD_SEC", "message": "Unknown synthetic security"}
            return block

        supported = {"PX_LAST"} if historical else {"PX_LAST", "CRNCY", "NAME"}
        block["fieldExceptions"] = [
            {"fieldId": field, "errorInfo": {"category": "BAD_FLD", "message": "Unsupported fixture field"}}
            for field in request.fields if field not in supported
        ]
        if historical:
            block["fieldData"] = [
                {"date": day.isoformat(), "PX_LAST": close}
                for day, close in zip(self._dates, record["closes"])
                if "PX_LAST" in request.fields and request.start <= day <= request.end and close is not None
            ]
        else:
            values = {
                "PX_LAST": record["closes"][self._snapshot_index],
                "CRNCY": record["currency"],
                "NAME": record["name"],
            }
            block["fieldData"] = {field: values[field] for field in request.fields if field in supported}
            block["fieldAsOf"] = dict.fromkeys(block["fieldData"], self.snapshot_date.isoformat())
        return block
