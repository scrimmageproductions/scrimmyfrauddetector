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
import math
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

    def __init__(self, max_snippet_chars: int = 60_000, namespace: str = ""):
        self._records: dict[str, SourceRecord] = {}
        self._raw: dict[str, tuple[bytes, str]] = {}
        self._lock = threading.Lock()
        self._seq = itertools.count(1)
        self.max_snippet_chars = max_snippet_chars
        # Stateless deployments merge ledgers from many requests; a namespace keeps their IDs unique.
        self.namespace = f"{namespace}-" if namespace else ""

    def new_id(self, method: str, url: str, body: Any) -> str:
        digest = hashlib.sha256(f"{method} {url} {json.dumps(body, sort_keys=True, default=str)}".encode()).hexdigest()[:10]
        return f"SRC-{self.namespace}{next(self._seq):05d}-{digest}"

    def add(self, record: SourceRecord, raw: bytes | None = None, content_type: str = "application/json") -> SourceRecord:
        """Store a record and (optionally) the exact, untruncated response body it was hashed from."""
        with self._lock:
            self._records[record.record_id] = record
            if raw is not None:
                self._raw[record.record_id] = (raw, content_type)
        return record

    def raw_body(self, record_id: str) -> tuple[bytes, str] | None:
        return self._raw.get(record_id)

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

    def wait(self, host: str, sleep=time.sleep) -> None:
        interval = self.min_intervals.get(host, self.default_interval)
        with self._lock:
            now = time.monotonic()
            ready_at = max(now, self._next_allowed.get(host, 0.0))
            self._next_allowed[host] = ready_at + interval
        delay = ready_at - now
        if delay > 0:
            sleep(delay)

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
    "graph.mapillary.com": 0.5,
    "maps.googleapis.com": 0.2,
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
        binary: bool = False,
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
            self.limiter.wait(host, self.sleep)
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
        content_type = response.headers.get("Content-Type", "application/octet-stream").split(";")[0].strip()
        record.status_code = response.status_code
        record.response_sha256 = hashlib.sha256(raw).hexdigest()
        if binary:
            record.raw_snippet = {"content_type": content_type, "bytes": len(raw)}
            if response.status_code >= 400:
                record.error = f"HTTP {response.status_code}"
                self.ledger.add(record, raw, content_type)
                raise SourceAPIError(f"{source}: HTTP {response.status_code} for {recorded_url}", record)
            self.ledger.add(record, raw, content_type)
            return raw, record
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
            self.ledger.add(record, raw, content_type)
            raise SourceAPIError(f"{source}: HTTP {response.status_code} for {recorded_url}: {raw[:300]!r}", record)
        if payload is None:
            record.error = "non-JSON response"
            self.ledger.add(record, raw, content_type)
            raise SourceAPIError(f"{source}: non-JSON response for {recorded_url}", record)
        self.ledger.add(record, raw, content_type)
        return payload, record

    def request_bytes(self, source: str, method: str, url: str, *, params: dict[str, Any] | None = None,
                      redact_params: tuple[str, ...] = ()) -> tuple[bytes, SourceRecord]:
        """Fetch a binary resource (e.g. an image) with the same retry, throttling and ledger guarantees."""
        return self.request_json(source, method, url, params=params, redact_params=redact_params, binary=True)  # type: ignore[return-value]


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
    states: list[str] = field(default_factory=list)
    zip_codes: list[str] = field(default_factory=list)
    locations: list[dict[str, str]] = field(default_factory=list)  # explicit USASpending location objects (state + county FIPS)
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
        # Precedence: explicit locations (Strike Force counties) > ZIP codes > states. None of them = nationwide.
        if self.locations:
            f["recipient_locations"] = self.locations
        elif self.zip_codes:
            f["recipient_locations"] = [{"country": "USA", "zip": z} for z in self.zip_codes]
        elif self.state or self.states:
            states = sorted({st.upper() for st in [*self.states, *([self.state] if self.state else [])]})
            f["recipient_locations"] = [{"country": "USA", "state": st} for st in states]
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
    GEOGRAPHIES = "https://geocoding.geo.census.gov/geocoder/geographies"

    def __init__(self, http: ResilientHTTP, benchmark: str = "Public_AR_Current", vintage: str = "Current_Current"):
        self.http = http
        self.benchmark = benchmark
        self.vintage = vintage

    def geographies_one_line(self, address: str) -> tuple[dict, SourceRecord]:
        """One-line geocode that also returns county FIPS and census tract (used for judicial-district targeting)."""
        params = {"address": address, "benchmark": self.benchmark, "vintage": self.vintage, "format": "json"}
        return self.http.request_json("census_geocoder", "GET", f"{self.GEOGRAPHIES}/onelineaddress", params=params)


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


# --------------------------------------------------------------------------- #
# State open data (Socrata SODA API) -- public, no login; app token optional
# --------------------------------------------------------------------------- #


class SocrataClient:
    """Read-only client for state licensing datasets published on Socrata portals."""

    def __init__(self, http: ResilientHTTP, app_token: str | None = None):
        self.http = http
        self.app_token = app_token or os.environ.get("SOCRATA_APP_TOKEN")

    def _params(self, extra: dict[str, Any]) -> dict[str, Any]:
        return {**extra, **({"$$app_token": self.app_token} if self.app_token else {})}

    def dataset_columns(self, domain: str, dataset_id: str) -> tuple[list[dict[str, Any]], SourceRecord]:
        payload, record = self.http.request_json(
            "socrata", "GET", f"https://{domain}/api/views/{dataset_id}.json",
            params=self._params({}), redact_params=("$$app_token",),
        )
        return list(payload.get("columns") or []), record

    def query(self, domain: str, dataset_id: str, where: str, limit: int = 50) -> tuple[list[dict[str, Any]], SourceRecord]:
        payload, record = self.http.request_json(
            "socrata", "GET", f"https://{domain}/resource/{dataset_id}.json",
            params=self._params({"$where": where, "$limit": limit}), redact_params=("$$app_token",),
        )
        return (payload if isinstance(payload, list) else []), record


# --------------------------------------------------------------------------- #
# Street-level imagery (optional; operator supplies their own key/token)
# --------------------------------------------------------------------------- #


class GoogleStreetViewClient:
    """Google Street View Static API. Metadata requests are free; image requests are billed to the operator's key.

    Google Maps Platform terms restrict storing/caching content, so callers keep only the pano_id, capture date,
    request URL (key redacted) and a SHA-256 of the bytes as fetched, not the image itself.
    """

    BASE = "https://maps.googleapis.com/maps/api/streetview"

    def __init__(self, http: ResilientHTTP, api_key: str):
        self.http = http
        self.api_key = api_key

    def metadata(self, lat: float, lon: float, radius_m: int = 50) -> tuple[dict, SourceRecord]:
        params = {"location": f"{lat},{lon}", "radius": radius_m, "source": "outdoor", "key": self.api_key}
        return self.http.request_json("google_street_view", "GET", f"{self.BASE}/metadata", params=params, redact_params=("key",))

    def image(self, pano_id: str, heading: float, size: str = "640x640", fov: int = 80) -> tuple[bytes, SourceRecord]:
        params = {"pano": pano_id, "heading": round(heading, 1), "fov": fov, "pitch": 5, "size": size, "key": self.api_key}
        return self.http.request_bytes("google_street_view", "GET", self.BASE, params=params, redact_params=("key",))


class MapillaryClient:
    """Mapillary Graph API v4. Imagery is CC BY-SA 4.0, so it can be archived with attribution."""

    BASE = "https://graph.mapillary.com"

    def __init__(self, http: ResilientHTTP, access_token: str):
        self.http = http
        self.access_token = access_token

    def images_near(self, lat: float, lon: float, radius_m: int = 60, limit: int = 25) -> tuple[list[dict], SourceRecord]:
        dlat = radius_m / 111_320.0
        dlon = radius_m / (111_320.0 * max(0.1, math.cos(math.radians(lat))))
        params = {
            "access_token": self.access_token,
            "fields": "id,captured_at,computed_geometry,compass_angle,thumb_1024_url,is_pano",
            "bbox": f"{lon - dlon:.6f},{lat - dlat:.6f},{lon + dlon:.6f},{lat + dlat:.6f}",
            "limit": limit,
        }
        payload, record = self.http.request_json("mapillary", "GET", f"{self.BASE}/images", params=params, redact_params=("access_token",))
        return list(payload.get("data") or []), record

    def download(self, url: str) -> tuple[bytes, SourceRecord]:
        return self.http.request_bytes("mapillary", "GET", url)
