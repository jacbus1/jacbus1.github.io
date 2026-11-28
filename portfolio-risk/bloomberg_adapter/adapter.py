"""Normalize responses from the Bloomberg-style refdata transport."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import date
import math
from typing import Literal, Protocol
from uuid import uuid4


REFDATA_SERVICE = "//blp/refdata"
REFERENCE_REQUEST = "ReferenceDataRequest"
HISTORICAL_REQUEST = "HistoricalDataRequest"

Value = str | int | float | bool
Scope = Literal["request", "security", "field"]


@dataclass(frozen=True)
class Request:
    service: str
    operation: str
    securities: tuple[str, ...]
    fields: tuple[str, ...]
    start: date | None = None
    end: date | None = None

    def parameters(self) -> dict:
        parameters = {"securities": list(self.securities), "fields": list(self.fields)}
        if self.operation == HISTORICAL_REQUEST:
            if self.start is None or self.end is None:
                raise ValueError("Historical requests require start and end dates")
            parameters.update(
                startDate=self.start.isoformat().replace("-", ""),
                endDate=self.end.isoformat().replace("-", ""),
                periodicitySelection="DAILY",
            )
        return parameters


@dataclass(frozen=True)
class Event:
    kind: str
    correlation_id: str
    messages: tuple[Mapping, ...] = ()


@dataclass(frozen=True)
class Source:
    name: str
    mode: str
    dataset: str
    revision: str


class Transport(Protocol):
    source: Source

    def events(self, request: Request, correlation_id: str) -> Iterable[Event]: ...


@dataclass(frozen=True)
class Provenance:
    source: Source
    service: str
    request_type: str
    correlation_id: str


@dataclass(frozen=True)
class Observation:
    security: str
    field: str
    value: Value
    as_of: date
    provenance: Provenance


@dataclass(frozen=True)
class Issue:
    scope: Scope
    code: str
    message: str
    security: str | None = None
    field: str | None = None
    as_of: date | None = None


@dataclass(frozen=True)
class Result:
    observations: tuple[Observation, ...]
    issues: tuple[Issue, ...]
    provenance: Provenance


class RequestError(RuntimeError):
    def __init__(self, issue: Issue, provenance: Provenance):
        self.issue = issue
        self.provenance = provenance
        super().__init__(f"{issue.code}: {issue.message}")


def _identifiers(items: Iterable[str], label: str) -> tuple[str, ...]:
    if isinstance(items, str):
        raise ValueError(f"Provide {label} as a sequence of strings")
    values = tuple(items)
    if not values or any(not isinstance(value, str) or not value.strip() for value in values):
        raise ValueError(f"Provide at least one non-empty {label} identifier")
    return tuple(dict.fromkeys(value.strip() for value in values))


def _provider_issue(
    error: Mapping, scope: Scope, security: str | None = None, field: str | None = None
) -> Issue:
    return Issue(
        scope,
        str(error.get("category") or error.get("code") or "PROVIDER_ERROR"),
        str(error.get("message") or "The provider returned an error without a message"),
        security,
        field,
    )


class BloombergAdapter:
    """Collect matching response events and keep dates and provider errors visible."""

    def __init__(self, transport: Transport):
        self.transport = transport

    def reference(
        self,
        securities: Iterable[str],
        fields: Iterable[str],
        *,
        as_of: date,
        max_age_days: int = 0,
    ) -> Result:
        if type(as_of) is not date:
            raise ValueError("as_of must be a date")
        if type(max_age_days) is not int or max_age_days < 0:
            raise ValueError("max_age_days must be a non-negative integer")
        request = Request(
            REFDATA_SERVICE, REFERENCE_REQUEST,
            _identifiers(securities, "security"), _identifiers(fields, "field"),
        )
        return self._execute(request, as_of=as_of, max_age_days=max_age_days)

    def historical(
        self,
        securities: Iterable[str],
        fields: Iterable[str],
        *,
        start: date,
        end: date,
    ) -> Result:
        if type(start) is not date or type(end) is not date or start > end:
            raise ValueError("Provide dates with start on or before end")
        request = Request(
            REFDATA_SERVICE, HISTORICAL_REQUEST,
            _identifiers(securities, "security"), _identifiers(fields, "field"), start, end,
        )
        return self._execute(request)

    def _execute(self, request: Request, *, as_of: date | None = None, max_age_days: int = 0) -> Result:
        provenance = Provenance(self.transport.source, request.service, request.operation, uuid4().hex)
        observations: dict[tuple[str, str, date], Observation] = {}
        issues: list[Issue] = []
        try:
            for event in self.transport.events(request, provenance.correlation_id):
                if event.correlation_id != provenance.correlation_id:
                    continue
                if event.kind == "TIMEOUT":
                    raise RequestError(Issue("request", "TIMEOUT", "The request timed out"), provenance)
                if event.kind == "REQUEST_STATUS":
                    reason = event.messages[0].get("reason", {}) if event.messages else {}
                    raise RequestError(_provider_issue(reason, "request"), provenance)
                if event.kind not in {"PARTIAL_RESPONSE", "RESPONSE"}:
                    continue
                for message in event.messages:
                    if "responseError" in message:
                        raise RequestError(_provider_issue(message["responseError"], "request"), provenance)
                    blocks = message["securityData"]
                    if request.operation == HISTORICAL_REQUEST:
                        blocks = [blocks]
                    for block in blocks:
                        candidates, block_issues = self._read_block(
                            block, request, provenance, as_of, max_age_days,
                        )
                        issues.extend(block_issues)
                        for candidate in candidates:
                            key = (candidate.security, candidate.field, candidate.as_of)
                            prior = observations.get(key)
                            if prior and (type(prior.value) is not type(candidate.value) or
                                          prior.value != candidate.value):
                                raise RequestError(Issue(
                                    "request", "CONFLICTING_OBSERVATION",
                                    "Two response fragments disagree on the observation",
                                    candidate.security, candidate.field, candidate.as_of,
                                ), provenance)
                            observations[key] = candidate
                if event.kind == "RESPONSE":
                    break
            else:
                raise RequestError(Issue(
                    "request", "INCOMPLETE_RESPONSE", "The event stream ended before the final response",
                ), provenance)
        except (AttributeError, KeyError, TypeError, ValueError) as error:
            raise RequestError(Issue(
                "request", "MALFORMED_RESPONSE", "The event payload does not match the transport contract",
            ), provenance) from error

        security_errors = {issue.security for issue in issues if issue.scope == "security"}
        field_errors = {(issue.security, issue.field) for issue in issues if issue.scope == "field"}
        accounted_for = set(field_errors)
        accepted = []
        for key in sorted(observations):
            observation = observations[key]
            pair = (observation.security, observation.field)
            if observation.security not in security_errors:
                accepted.append(observation)
                accounted_for.add(pair)
        for security in request.securities:
            for field in request.fields:
                if security not in security_errors and (security, field) not in accounted_for:
                    issues.append(Issue("field", "NO_DATA", "No observation was returned", security, field))

        return Result(tuple(accepted), tuple(dict.fromkeys(issues)), provenance)

    @staticmethod
    def _read_block(
        block: Mapping, request: Request, provenance: Provenance, as_of: date | None,
        max_age_days: int,
    ) -> tuple[list[Observation], list[Issue]]:
        security = block["security"]
        if security not in request.securities:
            raise ValueError("The response includes an unexpected security")
        if "securityError" in block:
            return [], [_provider_issue(block["securityError"], "security", security)]

        issues = [
            _provider_issue(item["errorInfo"], "field", security, item["fieldId"])
            for item in block.get("fieldExceptions", ())
        ]
        failed_fields = {issue.field for issue in issues}
        observations = []
        if as_of is None:
            rows = block.get("fieldData", ())
        else:
            rows = (block.get("fieldData", {}),)
        field_dates = block.get("fieldAsOf", {})

        for row in rows:
            for field in request.fields:
                value = row.get(field)
                if value is None or field in failed_fields:
                    continue
                if type(value) not in {str, int, float, bool} or (isinstance(value, float) and not math.isfinite(value)):
                    raise ValueError("The response contains an unsupported field value")
                raw_date = row.get("date") if as_of is None else field_dates.get(field)
                if not raw_date:
                    issues.append(Issue("field", "UNDATED_VALUE", "No observation date was supplied", security, field))
                    continue
                day = date.fromisoformat(raw_date)
                if as_of is not None:
                    age = (as_of - day).days
                    if age < 0:
                        issues.append(Issue("field", "FUTURE_VALUE", "Value is newer than the valuation date", security, field, day))
                        continue
                    if age > max_age_days:
                        issues.append(Issue("field", "STALE_VALUE", "Value exceeds the allowed age in calendar days", security, field, day))
                        continue
                elif not request.start <= day <= request.end:
                    issues.append(Issue("field", "OUTSIDE_DATE_RANGE", "Value is outside the requested dates", security, field, day))
                    continue
                observations.append(Observation(security, field, value, day, provenance))
        return observations, issues
