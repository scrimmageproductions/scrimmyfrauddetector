"""Resilient clients for official, public, unauthenticated government/open-data APIs.

Sources used (all public; no login, CAPTCHA or scraping):

* USASpending.gov API v2           https://api.usaspending.gov/docs/endpoints
* CMS NPPES NPI Registry API v2.1  https://npiregistry.cms.hhs.gov/api-page
* U.S. Census Bureau Geocoder      https://geocoding.geo.census.gov/geocoder/
* OpenStreetMap Overpass API       https://wiki.openstreetmap.org/wiki/Overpass_API
* (optional) SAM.gov Entity API    https://open.gsa.gov/api/entity-api/  -- requires the operator's own free key

Every request goes through ``ResilientHTTP`` which applies per-host throttling,
retries with exponential backoff + jitter (honouring ``Retry-After``), timeouts,
and writes a ``SourceRecord`` to the shared ``EvidenceLedger`` so each finding
can cite the exact URL, timestamp and raw response it came from.
"""

from __future__ import annotations

import hashlib
import itertools
import json
import logging
import os
import random
import threading
import time
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import date
from typing import Any
from urllib.parse import urlencode, urlsplit

import httpx

from . import __version__
from .models import SourceRecord, utcnow

log = logging.getLogger(__name__)

DEFAULT_CONTACT = os.environ.get("SCRIMMY_CONTACT", "set SCRIMMY_CONTACT=<your email>")
USER_AGENT = f"scrimmy-fraud-detector/{__version__} (investigative research; contact: {DEFAULT_CONTACT})"

RETRYABLE_STATUS = {408, 425, 429, 500, 502, 503, 504}


class SourceAPIError(RuntimeError):
    def __init__(self, message: str, record: SourceRecord | None = None):
        super().__init__(message)
        self.record = record


# --------------------------------------------------------------------------- #
# Evidence ledger
# --------------------------------------------------------------------------- #


class EvidenceLedger:
    """Thread-safe, append-only store of every SourceRecord captured during a scan."""

    def __init__(self, max_snippet_chars: int = 60_000):
        self._records: dict[str, SourceRecord] = {}
        self._lock = threading.Lock()
        self._seq = itertools.count(1)
        self.max_snippet_chars = max_snippet_chars

    def new_id(self, method: str, url: str, body: Any) -> str:
        digest = hashlib.sha256(f"{method} {url} {json.dumps(body, sort_keys=True, default=str)}".encode()).hexdigest()[:10]
        return f"SRC-{next(self._seq):05d}-{digest}"

    def add(self, record: SourceRecord) -> SourceRecord:
        with self._lock:
            self._records[record.record_id] = record
        return record

    def get(self, record_id: str) -> SourceRecord | None:
        return self._records.get(record_id)

    def subset(self, ids: set[str] | list[str]) -> list[SourceRecord]:
        return [self._records[i] for i in sorted(set(ids)) if i in self._records]

    def all(self) -> list[SourceRecord]:
        return list(self._records.values())

    def __len__(self) -> int:
        return len(self._records)


def truncate_json(obj: Any, max_list: int = 25, max_str: int = 4_000, _depth: int = 0) -> Any:
    """Bound the size of a raw snapshot while keeping it valid, inspectable JSON."""
    if _depth > 12:
        return "<max depth>"
    if isinstance(obj, dict):
        return {k: truncate_json(v, max_list, max_str, _depth + 1) for k, v in obj.items()}
    if isinstance(obj, list):
        items = [truncate_json(v, max_list, max_str, _depth + 1) for v in obj[:max_list]]
        if len(obj) > max_list:
            items.append(f"<{len(obj) - max_list} more items truncated; see response_sha256>")
        return items
    if isinstance(obj, str) and len(obj) > max_str:
        return obj[:max_str] + f"<{len(obj) - max_str} chars truncated>"
    return obj


# --------------------------------------------------------------------------- #
# Networking core
# --------------------------------------------------------------------------- #


class HostRateLimiter:
    """Enforces a minimum interval between requests to each host (process-wide)."""

    def __init__(self, min_intervals: dict[str, float], default_interval: float = 0.25):
        self.min_intervals = min_intervals
        self.default_interval = default_interval
        self._next_allowed: dict[str, float] = {}
        self._lock = threading.Lock()

    def wait(self, host: str) -> None:
        interval = self.min_intervals.get(host, self.default_interval)
        with self._lock:
            now = time.monotonic()
            ready_at = max(now, self._next_allowed.get(host, 0.0))
            self._next_allowed[host] = ready_at + interval
        delay = ready_at - now
        if delay > 0:
            time.sleep(delay)

    def penalize(self, host: str, seconds: float) -> None:
        with self._lock:
            self._next_allowed[host] = max(self._next_allowed.get(host, 0.0), time.monotonic() + seconds)


DEFAULT_HOST_INTERVALS = {
    "api.usaspending.gov": 0.35,
    "npiregistry.cms.hhs.gov": 0.35,
    "geocoding.geo.census.gov": 0.25,
    # Overpass public instances ask for modest, serial usage.
    "overpass-api.de": 1.5,
    "overpass.kumi.systems": 1.5,
    "api.sam.gov": 1.0,
}


@dataclass
class RetryPolicy:
    max_attempts: int = 5
    backoff_base: float = 1.0
    backoff_cap: float = 30.0
    jitter: float = 0.5

    def delay(self, attempt: int, retry_after: str | None = None) -> float:
        if retry_after:
            try:
                return min(float(retry_after), self.backoff_cap * 2)
            except ValueError:
                pass
        return min(self.backoff_cap, self.backoff_base * (2 ** (attempt - 1))) + random.uniform(0, self.jitter)


@dataclass
class ResilientHTTP:
    ledger: EvidenceLedger
    timeout: float = 30.0
    retry: RetryPolicy = field(default_factory=RetryPolicy)
    limiter: HostRateLimiter = field(default_factory=lambda: HostRateLimiter(DEFAULT_HOST_INTERVALS))
    transport: httpx.BaseTransport | None = None
    sleep: Any = time.sleep
    _client: httpx.Client | None = None

    def __post_init__(self) -> None:
        self._client = httpx.Client(
            timeout=httpx.Timeout(self.timeout, connect=min(10.0, self.timeout)),
            headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
            follow_redirects=True,
            transport=self.transport,
        )

    def close(self) -> None:
        if self._client:
            self._client.close()

    def request_json(
        self,
        source: str,
        method: str,
        url: str,
        *,
        params: dict[str, Any] | None = None,
        json_body: Any | None = None,
        form: dict[str, str] | None = None,
        redact_params: tuple[str, ...] = (),
    ) -> tuple[Any, SourceRecord]:
        """Perform a request and return (parsed_json, SourceRecord). Raises SourceAPIError on final failure."""
        assert self._client is not None
        clean_params = {k: v for k, v in (params or {}).items() if v not in (None, "")}
        full_url = f"{url}?{urlencode(clean_params, doseq=True)}" if clean_params else url
        # The URL recorded in evidence never contains secrets (e.g. a SAM.gov api_key).
        recorded_params = {k: ("<redacted>" if k in redact_params else v) for k, v in clean_params.items()}
        recorded_url = f"{url}?{urlencode(recorded_params, doseq=True)}" if recorded_params else url
        body_for_record = json_body if json_body is not None else form
        record_id = self.ledger.new_id(method, recorded_url, body_for_record)
        host = urlsplit(url).hostname or ""

        last_error: str | None = None
        response: httpx.Response | None = None
        started = time.monotonic()
        attempt = 0
        for attempt in range(1, self.retry.max_attempts + 1):
            self.limiter.wait(host)
            try:
                response = self._client.request(method, full_url, json=json_body, data=form)
            except (httpx.TransportError, httpx.TimeoutException) as exc:
                last_error = f"{type(exc).__name__}: {exc}"
                response = None
                log.warning("%s %s attempt %d failed: %s", method, recorded_url, attempt, last_error)
            else:
                if response.status_code not in RETRYABLE_STATUS:
                    break
                last_error = f"HTTP {response.status_code}"
                log.warning("%s %s attempt %d -> %s", method, recorded_url, attempt, response.status_code)
            if attempt < self.retry.max_attempts:
                delay = self.retry.delay(attempt, response.headers.get("Retry-After") if response is not None else None)
                if response is not None and response.status_code == 429:
                    self.limiter.penalize(host, delay)
                self.sleep(delay)

        elapsed_ms = round((time.monotonic() - started) * 1000, 1)
        record = SourceRecord(
            record_id=record_id,
            source=source,
            method=method,
            url=recorded_url,
            request_body=body_for_record,
            retrieved_at=utcnow(),
            elapsed_ms=elapsed_ms,
            attempts=attempt,
        )
        if response is None:
            record.error = last_error or "no response"
            self.ledger.add(record)
            raise SourceAPIError(f"{source}: {record.error} for {recorded_url}", record)

        raw = response.content
        record.status_code = response.status_code
        record.response_sha256 = hashlib.sha256(raw).hexdigest()
        try:
            payload = response.json()
        except (json.JSONDecodeError, UnicodeDecodeError):
            payload = None
            record.raw_snippet = raw[:2000].decode("utf-8", "replace")
            record.truncated = len(raw) > 2000
        else:
            snippet = truncate_json(payload)
            record.truncated = snippet != payload
            if len(json.dumps(snippet, default=str)) > self.ledger.max_snippet_chars:
                snippet = truncate_json(payload, max_list=5, max_str=500)
                record.truncated = True
            record.raw_snippet = snippet

        if response.status_code >= 400:
            record.error = f"HTTP {response.status_code}"
            self.ledger.add(record)
            raise SourceAPIError(f"{source}: HTTP {response.status_code} for {recorded_url}: {raw[:300]!r}", record)
        if payload is None:
            record.error = "non-JSON response"
            self.ledger.add(record)
            raise SourceAPIError(f"{source}: non-JSON response for {recorded_url}", record)
        self.ledger.add(record)
        return payload, record


# --------------------------------------------------------------------------- #
# USASpending.gov
# --------------------------------------------------------------------------- #

_COMMON_AWARD_FIELDS = [
    "Award ID", "Recipient Name", "Recipient UEI", "recipient_id", "Awarding Agency",
    "Awarding Sub Agency", "Description", "generated_internal_id",
]

AWARD_GROUPS: dict[str, dict[str, Any]] = {
    "contracts": {
        "codes": ["A", "B", "C", "D"],
        "amount_field": "Award Amount",
        "fields": [*_COMMON_AWARD_FIELDS, "Award Amount", "Start Date", "End Date", "Contract Award Type", "NAICS", "PSC"],
        "date_field": "Start Date",
        "type_field": "Contract Award Type",
    },
    "grants": {
        "codes": ["02", "03", "04", "05"],
        "amount_field": "Award Amount",
        "fields": [*_COMMON_AWARD_FIELDS, "Award Amount", "Start Date", "End Date", "Award Type"],
        "date_field": "Start Date",
        "type_field": "Award Type",
    },
    "direct_payments": {
        "codes": ["06", "10"],
        "amount_field": "Award Amount",
        "fields": [*_COMMON_AWARD_FIELDS, "Award Amount", "Start Date", "End Date", "Award Type"],
        "date_field": "Start Date",
        "type_field": "Award Type",
    },
    "loans": {
        "codes": ["07", "08"],
        "amount_field": "Loan Value",
        "fields": [*_COMMON_AWARD_FIELDS, "Loan Value", "Issued Date", "Award Type"],
        "date_field": "Issued Date",
        "type_field": "Award Type",
    },
    "other": {
        "codes": ["09", "11", "-1"],
        "amount_field": "Award Amount",
        "fields": [*_COMMON_AWARD_FIELDS, "Award Amount", "Start Date", "End Date", "Award Type"],
        "date_field": "Start Date",
        "type_field": "Award Type",
    },
}
CONTRACT_GROUPS = {"contracts"}
ASSISTANCE_GROUPS = {"grants", "direct_payments", "loans", "other"}


@dataclass
class AwardSearchFilters:
    state: str | None = None
    zip_codes: list[str] = field(default_factory=list)
    naics_codes: list[str] = field(default_factory=list)
    psc_codes: list[str] = field(default_factory=list)
    program_numbers: list[str] = field(default_factory=list)
    min_award: float | None = None
    max_award: float | None = None
    start_date: date | None = None
    end_date: date | None = None
    recipient_search_text: str | None = None

    def to_api(self, group: str) -> dict[str, Any]:
        f: dict[str, Any] = {"award_type_codes": AWARD_GROUPS[group]["codes"]}
        start = self.start_date or date(2007, 10, 1)
        end = self.end_date or date.today()
        f["time_period"] = [{"start_date": start.isoformat(), "end_date": end.isoformat()}]
        if self.zip_codes:
            f["recipient_locations"] = [{"country": "USA", "zip": z} for z in self.zip_codes]
        elif self.state:
            f["recipient_locations"] = [{"country": "USA", "state": self.state.upper()}]
        if self.min_award is not None or self.max_award is not None:
            bound: dict[str, float] = {}
            if self.min_award is not None:
                bound["lower_bound"] = self.min_award
            if self.max_award is not None:
                bound["upper_bound"] = self.max_award
            f["award_amounts"] = [bound]
        # NAICS/PSC only exist on contracts; Assistance Listings only on assistance awards.
        # Applying both to one request would AND them and return nothing.
        if group in CONTRACT_GROUPS:
            if self.naics_codes:
                f["naics_codes"] = {"require": self.naics_codes}
            if self.psc_codes:
                f["psc_codes"] = self.psc_codes
        elif self.program_numbers:
            f["program_numbers"] = self.program_numbers
        if self.recipient_search_text:
            f["recipient_search_text"] = [self.recipient_search_text]
        return f


class USASpendingClient:
    BASE = "https://api.usaspending.gov/api/v2"

    def __init__(self, http: ResilientHTTP):
        self.http = http

    def search_awards_page(self, filters: AwardSearchFilters, group: str, page: int = 1, limit: int = 100) -> tuple[dict, SourceRecord]:
        cfg = AWARD_GROUPS[group]
        body = {
            "filters": filters.to_api(group),
            "fields": cfg["fields"],
            "page": page,
            "limit": min(limit, 100),
            "sort": cfg["amount_field"],
            "order": "desc",
            "subawards": False,
        }
        return self.http.request_json("usaspending", "POST", f"{self.BASE}/search/spending_by_award/", json_body=body)

    def iter_awards(self, filters: AwardSearchFilters, group: str, max_results: int) -> Iterator[tuple[dict, SourceRecord]]:
        """Yield (award_row, source_record) for up to ``max_results`` rows, largest awards first."""
        page, yielded = 1, 0
        while yielded < max_results:
            payload, record = self.search_awards_page(filters, group, page=page, limit=min(100, max_results - yielded))
            rows = payload.get("results") or []
            for row in rows:
                yield row, record
                yielded += 1
                if yielded >= max_results:
                    return
            meta = payload.get("page_metadata") or {}
            if not rows or not meta.get("hasNext"):
                return
            page += 1

    def recipient_profile(self, recipient_id: str) -> tuple[dict, SourceRecord]:
        return self.http.request_json("usaspending", "GET", f"{self.BASE}/recipient/{recipient_id}/", params={"year": "all"})

    def award_detail(self, generated_internal_id: str) -> tuple[dict, SourceRecord]:
        return self.http.request_json("usaspending", "GET", f"{self.BASE}/awards/{generated_internal_id}/")


# --------------------------------------------------------------------------- #
# CMS NPPES NPI Registry
# --------------------------------------------------------------------------- #


class NPPESClient:
    BASE = "https://npiregistry.cms.hhs.gov/api/"

    def __init__(self, http: ResilientHTTP):
        self.http = http

    def search_organizations(self, organization_name: str, state: str | None = None, postal_code: str | None = None, limit: int = 200) -> tuple[dict, SourceRecord]:
        params = {
            "version": "2.1",
            "enumeration_type": "NPI-2",
            "organization_name": organization_name,
            "state": state,
            "postal_code": postal_code,
            "limit": min(limit, 200),
        }
        payload, record = self.http.request_json("nppes", "GET", self.BASE, params=params)
        if isinstance(payload, dict) and payload.get("Errors"):
            # NPPES returns HTTP 200 with an "Errors" array for invalid criteria.
            record.error = json.dumps(payload["Errors"])[:500]
        return payload, record

    def lookup_npi(self, npi: str) -> tuple[dict, SourceRecord]:
        return self.http.request_json("nppes", "GET", self.BASE, params={"version": "2.1", "number": npi})


# --------------------------------------------------------------------------- #
# U.S. Census Geocoder
# --------------------------------------------------------------------------- #


class CensusGeocoderClient:
    BASE = "https://geocoding.geo.census.gov/geocoder/locations"

    def __init__(self, http: ResilientHTTP, benchmark: str = "Public_AR_Current"):
        self.http = http
        self.benchmark = benchmark

    def geocode_components(self, street: str, city: str | None, state: str | None, zip5: str | None) -> tuple[dict, SourceRecord]:
        params = {"street": street, "city": city, "state": state, "zip": zip5, "benchmark": self.benchmark, "format": "json"}
        return self.http.request_json("census_geocoder", "GET", f"{self.BASE}/address", params=params)

    def geocode_one_line(self, address: str) -> tuple[dict, SourceRecord]:
        params = {"address": address, "benchmark": self.benchmark, "format": "json"}
        return self.http.request_json("census_geocoder", "GET", f"{self.BASE}/onelineaddress", params=params)


# --------------------------------------------------------------------------- #
# OpenStreetMap Overpass
# --------------------------------------------------------------------------- #


class OverpassClient:
    DEFAULT_ENDPOINT = "https://overpass-api.de/api/interpreter"

    def __init__(self, http: ResilientHTTP, endpoint: str | None = None):
        self.http = http
        self.endpoint = endpoint or os.environ.get("SCRIMMY_OVERPASS_URL", self.DEFAULT_ENDPOINT)

    @staticmethod
    def build_query(lat: float, lon: float, radius_m: int) -> str:
        """Buildings, POIs and enclosing land-use polygons around a point. Read-only, small radius."""
        return (
            f"[out:json][timeout:25];"
            f"("
            f"way(around:{radius_m},{lat},{lon})[building];"
            f"node(around:{radius_m},{lat},{lon})[shop];"
            f"node(around:{radius_m},{lat},{lon})[office];"
            f"node(around:{radius_m},{lat},{lon})[amenity];"
            f"node(around:{radius_m},{lat},{lon})[craft];"
            f"node(around:{radius_m},{lat},{lon})[healthcare];"
            f"way(around:{radius_m},{lat},{lon})[shop];"
            f"way(around:{radius_m},{lat},{lon})[amenity];"
            f");"
            f"out tags center;"
            f"is_in({lat},{lon})->.a;"
            f"(way(pivot.a)[landuse];relation(pivot.a)[landuse];);"
            f"out tags;"
        )

    def features_near(self, lat: float, lon: float, radius_m: int = 40) -> tuple[dict, SourceRecord]:
        query = self.build_query(lat, lon, radius_m)
        return self.http.request_json("osm_overpass", "POST", self.endpoint, form={"data": query})


# --------------------------------------------------------------------------- #
# SAM.gov Entity API (optional; operator supplies their own free API key)
# --------------------------------------------------------------------------- #


class SAMEntityClient:
    BASE = "https://api.sam.gov/entity-information/v3/entities"

    def __init__(self, http: ResilientHTTP, api_key: str):
        self.http = http
        self.api_key = api_key

    def get_entity(self, uei: str) -> tuple[dict, SourceRecord]:
        params = {"api_key": self.api_key, "ueiSAM": uei, "includeSections": "entityRegistration,coreData"}
        return self.http.request_json("sam_entity", "GET", self.BASE, params=params, redact_params=("api_key",))
