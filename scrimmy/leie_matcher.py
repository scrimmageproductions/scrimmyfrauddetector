"""HHS-OIG List of Excluded Individuals/Entities (LEIE) matcher.

The LEIE is published by HHS-OIG as a monthly, public, plain-text CSV
("Updated LEIE Database", currently linked from https://oig.hhs.gov/exclusions/exclusions_list.asp).
The full file contains only exclusions currently in effect; reinstated parties are removed.

Download it yourself (``scrimmy leie-download``) or point ``--leie-csv`` at a copy.
The file's SHA-256 is recorded in every dossier so a reviewer can confirm which
monthly edition was screened.

Match strengths (only CONFIRMED_IDENTIFIER and STRONG are scored):

* CONFIRMED_IDENTIFIER: the entity's NPI equals an LEIE row's NPI.
* STRONG (person): normalized first+last name equal, same state, and city or ZIP or
  middle initial agree.
* STRONG (business): name similarity >= 92, same state, and city or ZIP agree.
* POSSIBLE: name agreement + same state only. Reported for review, never scored,
  because common names produce false matches.
"""

from __future__ import annotations

import csv
import hashlib
import io
import logging
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path

from .models import AwardeeEntity, LEIEMatch, LEIEMatchStrength, LEIEScreenResult
from .normalize import normalize_entity_name, normalize_person_name, normalize_zip, similarity

log = logging.getLogger(__name__)

LEIE_DOWNLOAD_URL = "https://oig.hhs.gov/exclusions/downloadables/UPDATED.csv"

# Canonical column -> accepted header spellings (case-insensitive). The published layout uses the first spelling.
COLUMN_ALIASES: dict[str, tuple[str, ...]] = {
    "LASTNAME": ("LASTNAME", "LAST_NAME", "LAST NAME"),
    "FIRSTNAME": ("FIRSTNAME", "FIRST_NAME", "FIRST NAME"),
    "MIDNAME": ("MIDNAME", "MIDDLE_NAME", "MIDDLENAME", "MIDDLE NAME"),
    "BUSNAME": ("BUSNAME", "BUSINESS_NAME", "BUSINESSNAME", "BUSINESS NAME"),
    "GENERAL": ("GENERAL",),
    "SPECIALTY": ("SPECIALTY",),
    "NPI": ("NPI",),
    "ADDRESS": ("ADDRESS",),
    "CITY": ("CITY",),
    "STATE": ("STATE",),
    "ZIP": ("ZIP", "ZIPCODE", "ZIP_CODE"),
    "EXCLTYPE": ("EXCLTYPE", "EXCL_TYPE", "EXCLUSION_TYPE"),
    "EXCLDATE": ("EXCLDATE", "EXCL_DATE", "EXCLUSION_DATE"),
    "REINDATE": ("REINDATE", "REIN_DATE", "REINSTATEMENT_DATE"),
}
REQUIRED = ("LASTNAME", "FIRSTNAME", "BUSNAME", "STATE", "EXCLDATE")


def _parse_leie_date(value: str | None) -> date | None:
    v = (value or "").strip()
    if not v or set(v) == {"0"}:
        return None
    for fmt in ("%Y%m%d", "%m/%d/%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(v, fmt).date()
        except ValueError:
            continue
    return None


@dataclass
class _Row:
    number: int
    raw: dict[str, str]
    person: str = ""
    first: str = ""
    last: str = ""
    middle_initial: str = ""
    business: str = ""
    state: str = ""
    city: str = ""
    zip5: str | None = None
    npi: str = ""


@dataclass
class LEIEMatcher:
    rows: list[_Row] = field(default_factory=list)
    by_last: dict[str, list[_Row]] = field(default_factory=lambda: defaultdict(list))
    by_bus_token: dict[str, list[_Row]] = field(default_factory=lambda: defaultdict(list))
    by_npi: dict[str, list[_Row]] = field(default_factory=lambda: defaultdict(list))
    path: str | None = None
    sha256: str | None = None
    business_threshold: float = 92.0

    # -- loading ----------------------------------------------------------- #

    @classmethod
    def from_csv(cls, path: str | Path) -> LEIEMatcher:
        data = Path(path).read_bytes()
        m = cls.from_bytes(data)
        m.path = str(path)
        return m

    @classmethod
    def from_bytes(cls, data: bytes) -> LEIEMatcher:
        m = cls(sha256=hashlib.sha256(data).hexdigest())
        text = data.decode("utf-8-sig", errors="replace")
        reader = csv.DictReader(io.StringIO(text))
        header = {h.strip().upper(): h for h in (reader.fieldnames or [])}
        colmap: dict[str, str] = {}
        for canon, aliases in COLUMN_ALIASES.items():
            for a in aliases:
                if a in header:
                    colmap[canon] = header[a]
                    break
        missing = [c for c in REQUIRED if c not in colmap]
        if missing:
            raise ValueError(f"LEIE file is missing required column(s) {missing}; found {list(header)}")
        for i, raw in enumerate(reader, start=2):  # row 1 is the header
            get = lambda c, raw=raw: (raw.get(colmap[c]) or "").strip() if c in colmap else ""  # noqa: E731
            if _parse_leie_date(get("REINDATE")):
                continue  # reinstated: no longer excluded
            row = _Row(number=i, raw={k: (v or "") for k, v in raw.items() if k is not None})
            first, last, mid = get("FIRSTNAME"), get("LASTNAME"), get("MIDNAME")
            if last:
                row.person = normalize_person_name(f"{first} {last}")
                row.first, row.last = first.upper(), last.upper()
                row.middle_initial = mid[:1].upper()
            row.business = normalize_entity_name(get("BUSNAME"))
            row.state = get("STATE").upper()
            row.city = get("CITY").upper()
            row.zip5 = normalize_zip(get("ZIP"))[0]
            npi = get("NPI")
            row.npi = npi if npi and set(npi) != {"0"} else ""
            m.rows.append(row)
            if row.last:
                m.by_last[row.last].append(row)
            for tok in row.business.split()[:2]:
                if len(tok) >= 3:
                    m.by_bus_token[tok].append(row)
            if row.npi:
                m.by_npi[row.npi].append(row)
        log.info("Loaded %d active LEIE exclusions (sha256 %s)", len(m.rows), m.sha256[:12] if m.sha256 else "?")
        return m

    # -- matching ---------------------------------------------------------- #

    def _match(self, row: _Row, subject: str, role: str, strength: LEIEMatchStrength, sim: float, on: list[str]) -> LEIEMatch:
        return LEIEMatch(
            subject=subject, subject_role=role, strength=strength, similarity=round(sim, 1), matched_on=on,
            leie_row=row.raw, leie_row_number=row.number,
            exclusion_type=row.raw.get("EXCLTYPE") or None, exclusion_date=_parse_leie_date(row.raw.get("EXCLDATE")),
        )

    def match_person(self, name: str, role: str, state: str | None, city: str | None, zip5: str | None,
                     middle_initial: str | None = None) -> list[LEIEMatch]:
        norm = normalize_person_name(name)
        tokens = norm.split()
        if len(tokens) < 2 or not state:
            return []
        out = []
        candidates = {id(r): r for t in tokens for r in self.by_last.get(t, [])}
        for row in candidates.values():
            if row.person != norm or row.state != state.upper():
                continue
            on = ["first_name", "last_name", "state"]
            if city and row.city and row.city == city.upper():
                on.append("city")
            if zip5 and row.zip5 == zip5:
                on.append("zip")
            if middle_initial and row.middle_initial and row.middle_initial == middle_initial.upper():
                on.append("middle_initial")
            strength = LEIEMatchStrength.STRONG if len(on) > 3 else LEIEMatchStrength.POSSIBLE
            out.append(self._match(row, name, role, strength, 100.0, on))
        return out

    def match_business(self, name: str, state: str | None, city: str | None, zip5: str | None) -> list[LEIEMatch]:
        norm = normalize_entity_name(name)
        if not norm or not state:
            return []
        out = []
        candidates = {id(r): r for t in norm.split()[:2] for r in self.by_bus_token.get(t, [])}
        for row in candidates.values():
            if not row.business or row.state != state.upper():
                continue
            sim = similarity(norm, row.business)
            if sim < self.business_threshold:
                continue
            on = ["business_name", "state"]
            if city and row.city == city.upper():
                on.append("city")
            if zip5 and row.zip5 == zip5:
                on.append("zip")
            strength = LEIEMatchStrength.STRONG if len(on) > 2 else LEIEMatchStrength.POSSIBLE
            out.append(self._match(row, name, "entity", strength, sim, on))
        return out

    def match_npi(self, npi: str, subject: str) -> list[LEIEMatch]:
        return [self._match(r, subject, "entity_npi", LEIEMatchStrength.CONFIRMED_IDENTIFIER, 100.0, ["npi"])
                for r in self.by_npi.get(npi, [])]

    def screen(self, entity: AwardeeEntity, npis: list[str] | None = None) -> LEIEScreenResult:
        a = entity.address
        result = LEIEScreenResult(performed=True, dataset_path=self.path, dataset_sha256=self.sha256, dataset_rows=len(self.rows))
        matches: list[LEIEMatch] = []
        subjects = [entity.name, *entity.alternate_names]
        for nm in subjects:
            matches += self.match_business(nm, a.state, a.city, a.zip5)
        for npi in npis or []:
            subjects.append(f"NPI {npi}")
            matches += self.match_npi(npi, f"{entity.name} (NPI {npi})")
        for off in entity.officers:
            subjects.append(off.name)
            parts = off.name.replace(",", " ").split()
            mid = next((p.strip(".") for p in parts[1:-1] if len(p.strip(".")) == 1), None)
            matches += self.match_person(off.name, off.role_source, a.state, a.city, a.zip5, mid)
        seen: set[tuple[int, str]] = set()
        result.matches = [m for m in matches if not ((m.leie_row_number, m.subject) in seen or seen.add((m.leie_row_number, m.subject)))]
        result.subjects_screened = subjects
        return result
