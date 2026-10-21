"""State licensing lookups: licensed facility capacity from public state open data.

Two source kinds, both configured by the operator in a JSON file (see
``config/license_sources.example.json``):

* ``socrata``: a public Socrata (SODA) dataset, queried live with no login.
* ``csv``: a file the operator exported from a state licensing lookup. Many states
  publish licensing data only as downloads or search pages, and some states' search
  pages sit behind CAPTCHAs. Those pages are never scraped; export what the state
  allows and point a ``csv`` source at it.

Column names differ between states and change over time, so each source may give
an explicit ``fields`` mapping. Any field it leaves out is auto-detected from the
dataset's column metadata, and the mapping actually used is recorded in the evidence.
"""

from __future__ import annotations

import csv
import json
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .clients import SocrataClient, SourceAPIError
from .models import Address, AwardeeEntity, LicenseLookupResult, LicenseRecord
from .normalize import normalize_address, normalize_entity_name, similarity

log = logging.getLogger(__name__)

FIELD_KEYS = ("name", "address", "city", "zip", "capacity", "status", "license_type", "license_number")

# Ordered patterns: the first column whose lower-cased field name matches wins.
_AUTODETECT: dict[str, list[str]] = {
    "name": [r"^(operation|facility|provider|program|business|center|site)[_ ]?name$", r"^name$", r"(operation|facility|provider|program)[_ ]?name", r"name$"],
    "capacity": [r"^(total_)?licensed_?capacity$", r"^total_?capacity$", r"^capacity$", r"capacity"],
    "zip": [r"^zip(_?code)?$", r"^(postal|zip)", r"zip"],
    "address": [r"^(street_)?address(_line)?_?1?$", r"^(location_)?address$", r"street", r"address(?!.*(email|web|mail))"],
    "city": [r"^city$", r"(^|[_ ])city([_ ]|$)"],
    "status": [r"^(license_|operation_|facility_)?status$", r"(^|[_ ])status([_ ]|$)"],
    "license_type": [r"^(license|operation|facility|program|provider)[_ ]?type$", r"(^|[_ ])type([_ ]|$)"],
    "license_number": [r"^(license|operation|facility)[_ ]?(number|no|num|id)$", r"license.*(number|no|id)", r"operation_?id"],
}


def autodetect_fields(columns: list[str]) -> dict[str, str]:
    lowered = {c.lower(): c for c in columns}
    mapping: dict[str, str] = {}
    used: set[str] = set()
    for key in FIELD_KEYS:
        for pattern in _AUTODETECT[key]:
            hit = next((orig for low, orig in lowered.items() if orig not in used and re.search(pattern, low)), None)
            if hit:
                mapping[key] = hit
                used.add(hit)
                break
    return mapping


@dataclass
class LicenseSource:
    key: str
    kind: str  # 'socrata' | 'csv'
    states: list[str]
    verticals: list[str] = field(default_factory=list)
    domain: str | None = None
    dataset_id: str | None = None
    path: str | None = None
    fields: dict[str, str] = field(default_factory=dict)
    note: str | None = None
    _rows: list[dict[str, str]] | None = None

    @property
    def label(self) -> str:
        return f"socrata:{self.domain}/{self.dataset_id}" if self.kind == "socrata" else f"operator_csv:{Path(self.path or '').name}"

    def applies(self, state: str | None, vertical: str | None) -> bool:
        return bool(state) and state.upper() in {s.upper() for s in self.states} and (
            not self.verticals or vertical is None or vertical in self.verticals
        )


def load_license_sources(path: str | Path) -> list[LicenseSource]:
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    base = Path(path).parent
    out = []
    for item in raw.get("sources", []):
        src = LicenseSource(
            key=item["key"], kind=item["kind"], states=item.get("states", []), verticals=item.get("verticals", []),
            domain=item.get("domain"), dataset_id=item.get("dataset_id"), fields=item.get("fields", {}), note=item.get("note"),
            path=str((base / item["path"]).resolve()) if item.get("path") else None,
        )
        if src.kind not in ("socrata", "csv"):
            raise ValueError(f"license source {src.key}: unknown kind {src.kind!r}")
        out.append(src)
    return out


def _parse_capacity(value: Any) -> int | None:
    if value is None:
        return None
    m = re.search(r"\d[\d,]*", str(value))
    return int(m.group(0).replace(",", "")) if m else None


def _soql_quote(text: str) -> str:
    return text.replace("'", "''")


class LicenseLookup:
    def __init__(self, sources: list[LicenseSource], socrata: SocrataClient | None,
                 accept_name: float = 85.0, accept_name_with_address: float = 70.0):
        self.sources = sources
        self.socrata = socrata
        self.accept_name = accept_name
        self.accept_name_with_address = accept_name_with_address
        self._socrata_fields: dict[str, dict[str, str]] = {}

    # -- per-source field mapping ------------------------------------------ #

    def _fields_for(self, src: LicenseSource, record_ids: list[str]) -> dict[str, str]:
        if src.kind == "csv":
            rows = self._csv_rows(src)
            return {**autodetect_fields(list(rows[0].keys()) if rows else []), **src.fields}
        if src.key not in self._socrata_fields:
            detected: dict[str, str] = {}
            if self.socrata and set(FIELD_KEYS) - set(src.fields):
                cols, rec = self.socrata.dataset_columns(src.domain or "", src.dataset_id or "")
                record_ids.append(rec.record_id)
                detected = autodetect_fields([c.get("fieldName") for c in cols if c.get("fieldName")])
            self._socrata_fields[src.key] = {**detected, **src.fields}
        return self._socrata_fields[src.key]

    @staticmethod
    def _csv_rows(src: LicenseSource) -> list[dict[str, str]]:
        if src._rows is None:
            with open(src.path or "", newline="", encoding="utf-8-sig") as fh:
                src._rows = [dict(r) for r in csv.DictReader(fh)]
        return src._rows

    # -- candidate retrieval ----------------------------------------------- #

    @staticmethod
    def _name_token(entity: AwardeeEntity) -> str | None:
        stop = {"CHILD", "CARE", "CENTER", "HOME", "HEALTH", "SERVICES", "LEARNING", "ACADEMY", "DAYCARE", "DAY", "HOSPICE", "CLINIC"}
        tokens = [t for t in entity.normalized_name.split() if len(t) >= 4 and t not in stop]
        return max(tokens, key=len) if tokens else (entity.normalized_name.split() or [None])[0]

    def _socrata_candidates(self, src: LicenseSource, f: dict[str, str], entity: AwardeeEntity, record_ids: list[str]) -> list[dict]:
        assert self.socrata is not None
        zip5 = entity.address.zip5
        clauses = []
        if "zip" in f and zip5:
            clauses.append(f"starts_with({f['zip']}, '{_soql_quote(zip5)}')")
        token = self._name_token(entity)
        rows: list[dict] = []
        if "name" in f and token:
            where = " AND ".join([*clauses, f"upper({f['name']}) like '%{_soql_quote(token)}%'"])
            rows, rec = self.socrata.query(src.domain or "", src.dataset_id or "", where)
            record_ids.append(rec.record_id)
        if not rows and clauses:
            rows, rec = self.socrata.query(src.domain or "", src.dataset_id or "", clauses[0], limit=500)
            record_ids.append(rec.record_id)
        return rows

    def _csv_candidates(self, src: LicenseSource, f: dict[str, str], entity: AwardeeEntity) -> list[dict]:
        rows = self._csv_rows(src)
        zip5 = entity.address.zip5
        if "zip" in f and zip5:
            return [r for r in rows if str(r.get(f["zip"], "")).startswith(zip5)]
        return rows

    # -- public API -------------------------------------------------------- #

    def lookup(self, entity: AwardeeEntity, vertical: str | None) -> LicenseLookupResult:
        result = LicenseLookupResult()
        applicable = [s for s in self.sources if s.applies(entity.address.state, vertical)]
        if not applicable:
            return result
        result.performed = True
        best: LicenseRecord | None = None
        errors = []
        for src in applicable:
            record_ids: list[str] = []
            result.sources_queried.append(src.label)
            try:
                f = self._fields_for(src, record_ids)
                if "name" not in f:
                    raise ValueError(f"could not identify a facility-name column; set 'fields.name' for source {src.key}")
                rows = self._socrata_candidates(src, f, entity, record_ids) if src.kind == "socrata" else self._csv_candidates(src, f, entity)
            except (SourceAPIError, OSError, ValueError) as exc:
                errors.append(f"{src.label}: {exc}")
                result.source_record_ids += record_ids
                continue
            result.source_record_ids += record_ids
            for row in rows:
                result.candidates_examined += 1
                cand = self._to_record(src, f, row, entity, record_ids)
                if cand and (best is None or (cand.name_similarity, cand.address_match) > (best.name_similarity, best.address_match)):
                    best = cand
        if best and (best.name_similarity >= self.accept_name or (best.address_match and best.name_similarity >= self.accept_name_with_address)):
            result.best_match = best
        if errors and not result.best_match:
            result.error = " | ".join(errors)
        return result

    def _to_record(self, src: LicenseSource, f: dict[str, str], row: dict, entity: AwardeeEntity, record_ids: list[str]) -> LicenseRecord | None:
        name = str(row.get(f["name"], "") or "").strip()
        if not name:
            return None
        addr = normalize_address(Address(
            line1=row.get(f["address"]) if "address" in f else None, city=row.get(f["city"]) if "city" in f else None,
            state=entity.address.state, zip5=str(row.get(f["zip"])) if "zip" in f and row.get(f["zip"]) else None,
        ))
        address_match = bool(addr.zip5 and addr.zip5 == entity.address.zip5 and addr.normalized_street and entity.address.normalized_street
                             and similarity(addr.normalized_street, entity.address.normalized_street) >= 85)
        return LicenseRecord(
            source=src.label,
            license_number=str(row.get(f["license_number"])) if "license_number" in f and row.get(f["license_number"]) else None,
            facility_name=name,
            license_type=str(row.get(f["license_type"])) if "license_type" in f and row.get(f["license_type"]) else None,
            status=str(row.get(f["status"])) if "status" in f and row.get(f["status"]) else None,
            capacity=_parse_capacity(row.get(f["capacity"])) if "capacity" in f else None,
            address=addr,
            name_similarity=similarity(normalize_entity_name(name), entity.normalized_name),
            address_match=address_match,
            raw={"field_mapping": f, "row": {k: v for k, v in row.items() if not isinstance(v, (dict, list))}},
            source_record_ids=list(record_ids),
        )
