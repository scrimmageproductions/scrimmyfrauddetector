"""Stage 2 - Enrichment (Cross-Referencer).

* NPPES NPI Registry: organizational NPI match, active status, authorized official.
* U.S. Census Geocoder: does the address of record resolve to a real street segment?
* OpenStreetMap Overpass: what is mapped at/around the geocoded point
  (residential building, commercial POIs, mail-drop storefronts, nothing at all).
* Optional formation-date sources: SAM.gov entity API (operator's key) or an
  operator-supplied CSV of manually retrieved Secretary-of-State filings.
"""

from __future__ import annotations

import csv
import logging
import math
import re
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

from .clients import CensusGeocoderClient, NPPESClient, OverpassClient, SAMEntityClient, SourceAPIError
from .ingestion import parse_date
from .models import (
    Address,
    AwardeeEntity,
    EnrichmentData,
    GeocodeResult,
    LandUseClass,
    LandUseResult,
    NPIMatchResult,
    NPIRecord,
    Officer,
)
from .normalize import normalize_address, normalize_entity_name, normalize_person_name, similarity
from .verticals import HEALTHCARE_BUSINESS_TYPE_KEYWORDS, HEALTHCARE_NAICS_PREFIXES, Vertical

log = logging.getLogger(__name__)

# --------------------------------------------------------------------------- #
# OSM tag vocabularies
# --------------------------------------------------------------------------- #

RESIDENTIAL_BUILDINGS = {
    "house", "detached", "residential", "semidetached_house", "terrace", "bungalow", "apartments",
    "duplex", "cabin", "farm", "static_caravan", "dormitory", "houseboat", "triplex",
}
COMMERCIAL_BUILDINGS = {
    "commercial", "retail", "office", "industrial", "warehouse", "supermarket", "kiosk", "hotel",
    "hospital", "clinic", "school", "kindergarten", "college", "university", "church", "civic",
    "government", "public", "train_station", "transportation", "manufacture", "service", "storage",
}
COMMERCIAL_LANDUSE = {"commercial", "retail", "industrial", "institutional", "education"}
RESIDENTIAL_LANDUSE = {"residential"}
UNDEVELOPED_LANDUSE = {"grass", "greenfield", "brownfield", "meadow", "farmland", "construction", "forest", "scrub"}
# Amenity values that do not indicate an operating business at the address.
NON_BUSINESS_AMENITIES = {
    "bench", "parking", "parking_space", "parking_entrance", "bicycle_parking", "waste_basket", "waste_disposal",
    "recycling", "post_box", "vending_machine", "telephone", "drinking_water", "shelter", "fountain", "clock",
    "loading_dock", "charging_station", "motorcycle_parking", "atm", "letter_box", "grit_bin", "toilets",
}
MAIL_DROP_NAME_RE = re.compile(
    r"\b(UPS\s*STORE|POSTNET|POSTAL\s*ANNEX|POSTALANNEX|MAIL\s*BOXES\s*ETC|PAK\s*MAIL|POSTAL\s*CONNECTIONS|"
    r"FEDEX\s*OFFICE|PACK\s*(AND|&|N)\s*SHIP|PAKMAIL|MAILBOX(ES)?\s*(PLUS|CENTER|RENTAL)?|VIRTUAL\s*OFFICE|IPOSTAL|"
    r"ANYTIME\s*MAILBOX|EARTH\s*CLASS\s*MAIL|REGUS|SHIPPING\s*(CENTER|STORE))\b",
    re.I,
)


def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6_371_000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = math.radians(lat2 - lat1), math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def _element_point(el: dict[str, Any]) -> tuple[float, float] | None:
    if "lat" in el and "lon" in el:
        return el["lat"], el["lon"]
    c = el.get("center")
    if c and "lat" in c:
        return c["lat"], c["lon"]
    return None


def _label(tags: dict[str, str]) -> str:
    kind = next((f"{k}={tags[k]}" for k in ("shop", "office", "amenity", "healthcare", "craft", "building") if k in tags), "feature")
    name = tags.get("name") or tags.get("brand")
    return f"{kind} ({name})" if name else kind


def classify_land_use(address: Address, lat: float, lon: float, elements: list[dict[str, Any]], radius_m: int) -> LandUseResult:
    """Deterministic classification of what OSM shows at a geocoded point. Pure function (unit-tested)."""
    result = LandUseResult(performed=True, search_radius_m=radius_m)
    buildings: list[tuple[float, str]] = []
    commercial: list[str] = []
    mail_drops: list[str] = []
    landuse: list[str] = []

    for el in elements:
        tags = el.get("tags") or {}
        if "landuse" in tags and not _element_point(el):
            landuse.append(tags["landuse"])
            continue
        pt = _element_point(el)
        dist = haversine_m(lat, lon, *pt) if pt else float(radius_m)
        name_blob = " ".join(filter(None, (tags.get("name"), tags.get("brand"), tags.get("operator"))))
        is_mail_drop = bool(MAIL_DROP_NAME_RE.search(name_blob)) or tags.get("shop") in {"shipping", "mailbox"} or (
            tags.get("amenity") == "post_office" and tags.get("post_office") in {"bureau", "partner"}
        )
        if is_mail_drop:
            mail_drops.append(f"{_label(tags)} ~{dist:.0f}m")
        if "building" in tags and el.get("type") in ("way", "relation"):
            buildings.append((dist, tags["building"]))
        if any(k in tags for k in ("shop", "office", "craft", "healthcare")) or (
            "amenity" in tags and tags["amenity"] not in NON_BUSINESS_AMENITIES
        ):
            if not is_mail_drop:
                commercial.append(f"{_label(tags)} ~{dist:.0f}m")

    buildings.sort()
    nearest = buildings[0][1] if buildings else None
    result.nearest_building_tag = nearest
    result.landuse_tags = sorted(set(landuse))
    result.commercial_pois = commercial
    result.mail_drop_pois = mail_drops
    signals: list[str] = []
    has_unit = bool(address.normalized_unit)

    def done(cls: LandUseClass) -> LandUseResult:
        result.classification = cls
        result.signals = [s for s in signals if s]
        return result

    if address.is_po_box:
        signals.append("Address of record is a USPS Post Office Box (no physical premises).")
        return done(LandUseClass.PO_BOX)
    if address.is_pmb:
        signals.append("Address of record contains a Private Mailbox (PMB) designator, the USPS format for CMRA mail drops.")
        if mail_drops:
            signals.append(f"Mapped mail-drop storefront(s) within {radius_m}m: {', '.join(mail_drops)}.")
        return done(LandUseClass.CMRA_MAIL_DROP)
    if mail_drops and has_unit:
        signals.append(
            f"Address of record uses a unit designator ({address.normalized_unit}) and a mail-drop storefront is mapped "
            f"within {radius_m}m: {', '.join(mail_drops)}. Pattern is consistent with a CMRA box addressed as a suite."
        )
        return done(LandUseClass.CMRA_MAIL_DROP)
    if mail_drops:
        signals.append(f"Mail-drop storefront mapped nearby ({', '.join(mail_drops)}), but the address has no unit/PMB; informational only.")

    land_res = bool(set(landuse) & RESIDENTIAL_LANDUSE)
    land_com = bool(set(landuse) & COMMERCIAL_LANDUSE)

    if nearest in RESIDENTIAL_BUILDINGS:
        signals.append(f"Nearest mapped structure is tagged building={nearest} (residential).")
        if commercial:
            signals.append(f"Commercial POIs also mapped within {radius_m}m: {', '.join(commercial[:5])}.")
            return done(LandUseClass.MIXED)
        signals.append(f"No commercial, office or service POIs mapped within {radius_m}m.")
        return done(LandUseClass.RESIDENTIAL)
    if nearest in COMMERCIAL_BUILDINGS or (commercial and nearest is not None):
        signals.append(f"Nearest mapped structure is tagged building={nearest}.")
        if commercial:
            signals.append(f"Commercial POIs within {radius_m}m: {', '.join(commercial[:5])}.")
        return done(LandUseClass.COMMERCIAL)
    if nearest is not None:  # generic building=yes etc.
        signals.append(f"Nearest mapped structure has a generic tag building={nearest}.")
        if land_res and not land_com:
            signals.append("Point lies inside a landuse=residential area with no commercial POIs mapped nearby.")
            return done(LandUseClass.RESIDENTIAL)
        if land_com:
            signals.append("Point lies inside commercial/institutional land use.")
            return done(LandUseClass.COMMERCIAL)
        signals.append("No enclosing land-use polygon mapped; structure type cannot be determined from OSM.")
        return done(LandUseClass.UNKNOWN)
    # No building mapped near the point.
    if commercial:
        signals.append(f"No building footprint mapped, but commercial POIs within {radius_m}m: {', '.join(commercial[:5])}.")
        return done(LandUseClass.COMMERCIAL)
    if set(landuse) & UNDEVELOPED_LANDUSE or not landuse:
        signals.append(
            f"No building footprint or business POI mapped within {radius_m}m"
            + (f"; enclosing land use: {', '.join(sorted(set(landuse)))}." if landuse else ".")
            + " OSM coverage can be incomplete; treat as unverified."
        )
        return done(LandUseClass.NO_STRUCTURE_MAPPED)
    if land_res:
        signals.append("No building footprint mapped; point lies inside landuse=residential.")
        return done(LandUseClass.RESIDENTIAL)
    signals.append("Insufficient OSM tagging to classify.")
    return done(LandUseClass.UNKNOWN)


# --------------------------------------------------------------------------- #
# Formation dates
# --------------------------------------------------------------------------- #


@dataclass
class FormationDateRecord:
    formation_date: date
    source_note: str


@dataclass
class OperatorFormationIndex:
    """Formation dates manually retrieved by the operator (e.g. from a Secretary of State business search).

    CSV columns: ``uei`` and/or ``name``, ``formation_date`` (YYYY-MM-DD), optional ``source_note``.
    """

    by_uei: dict[str, FormationDateRecord] = field(default_factory=dict)
    by_name: dict[str, FormationDateRecord] = field(default_factory=dict)
    path: str | None = None

    @classmethod
    def load(cls, path: str | Path) -> OperatorFormationIndex:
        idx = cls(path=str(path))
        with open(path, newline="", encoding="utf-8-sig") as fh:
            for row in csv.DictReader(fh):
                d = parse_date(row.get("formation_date"))
                if not d:
                    continue
                rec = FormationDateRecord(d, row.get("source_note") or f"operator CSV {Path(path).name}")
                if row.get("uei"):
                    idx.by_uei[row["uei"].strip().upper()] = rec
                if row.get("name"):
                    idx.by_name[normalize_entity_name(row["name"])] = rec
        return idx

    def lookup(self, entity: AwardeeEntity) -> FormationDateRecord | None:
        if entity.uei and entity.uei in self.by_uei:
            return self.by_uei[entity.uei]
        return self.by_name.get(entity.normalized_name)


# --------------------------------------------------------------------------- #
# Cross-referencer
# --------------------------------------------------------------------------- #


@dataclass
class EnrichmentConfig:
    osm_radius_m: int = 40
    npi_name_threshold: float = 90.0
    npi_name_threshold_with_address: float = 80.0
    npi_for_all: bool = False
    formation_proxy_npi: bool = False
    skip_osm: bool = False


class CrossReferencer:
    def __init__(
        self,
        nppes: NPPESClient,
        geocoder: CensusGeocoderClient,
        overpass: OverpassClient,
        sam: SAMEntityClient | None = None,
        formation_index: OperatorFormationIndex | None = None,
        vertical: Vertical | None = None,
        config: EnrichmentConfig | None = None,
    ):
        self.nppes = nppes
        self.geocoder = geocoder
        self.overpass = overpass
        self.sam = sam
        self.formation_index = formation_index
        self.vertical = vertical
        self.config = config or EnrichmentConfig()
        self._geo_cache: dict[str, tuple[GeocodeResult, LandUseResult]] = {}

    def enrich(self, entity: AwardeeEntity) -> EnrichmentData:
        data = EnrichmentData()
        self._classify_healthcare(entity)
        self._formation_date(entity, data)
        data.geocode, data.land_use = self._locate(entity.address)
        if entity.is_healthcare or self.config.npi_for_all:
            data.npi = self._npi(entity)
            self._absorb_npi(entity, data)
        return data

    # -- healthcare -------------------------------------------------------- #

    def _classify_healthcare(self, entity: AwardeeEntity) -> None:
        reasons = []
        if self.vertical and self.vertical.healthcare:
            reasons.append(f"scan vertical '{self.vertical.key}'")
        naics = sorted({a.naics_code for a in entity.awards if a.naics_code and a.naics_code.startswith(HEALTHCARE_NAICS_PREFIXES)})
        if naics:
            reasons.append(f"award NAICS {', '.join(naics)}")
        bt = [b for b in entity.business_types if any(k in b.lower() for k in HEALTHCARE_BUSINESS_TYPE_KEYWORDS)]
        if bt:
            reasons.append(f"business type(s) {', '.join(bt)}")
        if reasons:
            entity.is_healthcare = True
            entity.healthcare_basis = "; ".join(reasons)

    # -- formation date ---------------------------------------------------- #

    def _formation_date(self, entity: AwardeeEntity, data: EnrichmentData) -> None:
        if self.formation_index:
            rec = self.formation_index.lookup(entity)
            if rec:
                entity.formation_date = rec.formation_date
                entity.formation_date_source = f"operator_csv: {rec.source_note}"
                return
        if self.sam and entity.uei:
            try:
                payload, record = self.sam.get_entity(entity.uei)
            except SourceAPIError as exc:
                data.notes.append(f"SAM.gov entity lookup failed: {exc}")
            else:
                ent = (payload.get("entityData") or [{}])[0]
                core = (ent.get("coreData") or {}).get("entityInformation") or {}
                reg = ent.get("entityRegistration") or {}
                start = parse_date(core.get("entityStartDate"))
                if start:
                    entity.formation_date = start
                    entity.formation_date_source = "sam.gov coreData.entityInformation.entityStartDate"
                    entity.formation_source_record_ids = [record.record_id]
                if reg.get("cageCode") and not entity.cage:
                    entity.cage = reg["cageCode"]
                if start:
                    return
        if not entity.formation_date:
            data.notes.append(
                "No business formation date available (USASpending does not publish one). Supply --sam-api-key or "
                "--formation-csv to evaluate the Newly Formed Windfall rule."
            )

    # -- location ---------------------------------------------------------- #

    def _locate(self, address: Address) -> tuple[GeocodeResult, LandUseResult]:
        key = address.location_key or address.one_line
        if key in self._geo_cache:
            return self._geo_cache[key]
        geo = GeocodeResult(input_address=address.one_line)
        land = LandUseResult()
        if address.is_po_box:
            land = classify_land_use(address, 0, 0, [], self.config.osm_radius_m)
            land.performed = False
            land.signals.append("Geocoding skipped: P.O. Boxes have no street location.")
        elif address.line1 and (address.zip5 or (address.city and address.state)):
            geo = self._geocode(address)
            if geo.matched and not self.config.skip_osm:
                land = self._land_use(address, geo)
            elif not geo.matched:
                land.signals.append("Address did not match a Census TIGER street segment; land use not evaluated.")
        self._geo_cache[key] = (geo, land)
        return geo, land

    def _geocode(self, address: Address) -> GeocodeResult:
        geo = GeocodeResult(performed=True, input_address=address.one_line)
        try:
            payload, record = self.geocoder.geocode_components(address.line1 or "", address.city, address.state, address.zip5)
        except SourceAPIError as exc:
            geo.error = str(exc)
            if exc.record:
                geo.source_record_ids = [exc.record.record_id]
            return geo
        geo.source_record_ids = [record.record_id]
        matches = ((payload or {}).get("result") or {}).get("addressMatches") or []
        if matches:
            m = matches[0]
            coords = m.get("coordinates") or {}
            tiger = m.get("tigerLine") or {}
            geo.matched = True
            geo.matched_address = m.get("matchedAddress")
            geo.longitude = coords.get("x")
            geo.latitude = coords.get("y")
            geo.tiger_line_id = str(tiger.get("tigerLineId")) if tiger.get("tigerLineId") else None
            geo.tiger_side = tiger.get("side")
        return geo

    def _land_use(self, address: Address, geo: GeocodeResult) -> LandUseResult:
        assert geo.latitude is not None and geo.longitude is not None
        try:
            payload, record = self.overpass.features_near(geo.latitude, geo.longitude, self.config.osm_radius_m)
        except SourceAPIError as exc:
            res = LandUseResult(performed=True, error=str(exc), search_radius_m=self.config.osm_radius_m)
            if exc.record:
                res.source_record_ids = [exc.record.record_id]
            return res
        res = classify_land_use(address, geo.latitude, geo.longitude, payload.get("elements") or [], self.config.osm_radius_m)
        res.source_record_ids = [record.record_id]
        return res

    # -- NPPES ------------------------------------------------------------- #

    def _npi_queries(self, entity: AwardeeEntity) -> list[str]:
        queries: list[str] = []
        for nm in [entity.normalized_name, *(normalize_entity_name(a) for a in entity.alternate_names)]:
            if nm and len(nm) >= 2:
                q = f"{nm}*"
                if q not in queries:
                    queries.append(q)
        tokens = entity.normalized_name.split()
        if len(tokens) > 2:
            q = f"{' '.join(tokens[:2])}*"
            if q not in queries:
                queries.append(q)
        return queries[:3]

    def _npi(self, entity: AwardeeEntity) -> NPIMatchResult:
        result = NPIMatchResult(performed=True)
        seen: dict[str, NPIRecord] = {}
        errors = []
        for query in self._npi_queries(entity):
            result.query_names.append(query)
            try:
                payload, record = self.nppes.search_organizations(query, state=entity.address.state)
            except SourceAPIError as exc:
                errors.append(str(exc))
                if exc.record:
                    result.source_record_ids.append(exc.record.record_id)
                continue
            result.source_record_ids.append(record.record_id)
            if record.error:
                errors.append(f"NPPES rejected query {query!r}: {record.error}")
                continue
            for raw in payload.get("results") or []:
                rec = self._npi_record(raw, entity)
                result.candidates_examined += 1
                if rec and rec.npi not in seen:
                    seen[rec.npi] = rec
            if any(self._accept(r) for r in seen.values()):
                break
        result.matches = sorted((r for r in seen.values() if self._accept(r)), key=lambda r: (-r.name_similarity, not r.address_match))
        result.best_match = result.matches[0] if result.matches else None
        result.active_npi_found = any(r.status == "A" for r in result.matches)
        if errors and not result.matches and len(errors) == len(result.query_names):
            result.error = " | ".join(errors)
        return result

    def _accept(self, rec: NPIRecord) -> bool:
        return rec.name_similarity >= self.config.npi_name_threshold or (
            rec.address_match and rec.name_similarity >= self.config.npi_name_threshold_with_address
        )

    @staticmethod
    def _npi_address(a: dict[str, Any]) -> Address:
        return normalize_address(
            Address(line1=a.get("address_1"), line2=a.get("address_2") or None, city=a.get("city"),
                    state=a.get("state"), zip5=a.get("postal_code"), country=a.get("country_code"))
        )

    def _npi_record(self, raw: dict[str, Any], entity: AwardeeEntity) -> NPIRecord | None:
        basic = raw.get("basic") or {}
        org = basic.get("organization_name")
        names = [org, *((o or {}).get("organization_name") for o in raw.get("other_names") or [])]
        sims = [similarity(normalize_entity_name(n), entity.normalized_name) for n in names if n]
        if not sims:
            return None
        addresses = raw.get("addresses") or []
        practice = next((a for a in addresses if a.get("address_purpose") == "LOCATION"), None)
        mailing = next((a for a in addresses if a.get("address_purpose") == "MAILING"), None)
        all_addrs = [self._npi_address(a) for a in [*addresses, *(raw.get("practiceLocations") or [])]]
        addr_match = any(
            a.zip5 == entity.address.zip5 and similarity(a.normalized_street or "", entity.address.normalized_street or "") >= 85
            for a in all_addrs
            if entity.address.zip5
        )
        official = " ".join(
            p for p in (basic.get("authorized_official_first_name"), basic.get("authorized_official_middle_name"),
                        basic.get("authorized_official_last_name")) if p
        ) or None
        return NPIRecord(
            npi=str(raw.get("number")),
            organization_name=org,
            status=basic.get("status"),
            enumeration_type=raw.get("enumeration_type"),
            enumeration_date=parse_date(basic.get("enumeration_date")),
            last_updated=parse_date(basic.get("last_updated")),
            authorized_official_name=official,
            authorized_official_title=basic.get("authorized_official_title_or_position"),
            taxonomies=[f"{t.get('code')} {t.get('desc')}".strip() for t in raw.get("taxonomies") or []],
            practice_address=self._npi_address(practice) if practice else None,
            mailing_address=self._npi_address(mailing) if mailing else None,
            name_similarity=max(sims),
            address_match=addr_match,
        )

    def _absorb_npi(self, entity: AwardeeEntity, data: EnrichmentData) -> None:
        best = data.npi.best_match
        if not best:
            return
        if best.authorized_official_name:
            norm = normalize_person_name(best.authorized_official_name)
            if norm and not any(o.normalized_name == norm for o in entity.officers):
                entity.officers = [
                    *entity.officers,
                    Officer(name=best.authorized_official_name, normalized_name=norm,
                            title=best.authorized_official_title or "Authorized official",
                            role_source="nppes.authorized_official", source_record_ids=list(data.npi.source_record_ids)),
                ]
        if self.config.formation_proxy_npi and not entity.formation_date and best.enumeration_date:
            entity.formation_date = best.enumeration_date
            entity.formation_date_source = "nppes.basic.enumeration_date (proxy - NPI issuance, not legal formation)"
            entity.formation_source_record_ids = list(data.npi.source_record_ids)
        if best.enumeration_date:
            first_award = min((a.effective_date for a in entity.awards if a.effective_date), default=None)
            if first_award and best.enumeration_date > first_award:
                data.notes.append(
                    f"NPI {best.npi} was enumerated on {best.enumeration_date}, after the earliest award date in scope "
                    f"({first_award}). Informational; not scored."
                )
