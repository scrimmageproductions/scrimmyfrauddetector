"""Orchestrates the pipeline.

    Ingestion -> Enrichment & state licensing -> Capacity arithmetic -> Network graph
      -> Deterministic scoring -> (vision site check for near-threshold leads, re-score)
      -> Evidence archiving -> Dossier generation

Vision checks cost money (imagery API + vision model), so they run only for entities
whose score is at, or could reach, the threshold if the site rule triggered. Every
other entity is scored without them, which is recorded as INSUFFICIENT_DATA.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

import httpx

from .clients import (
    AwardSearchFilters,
    CensusGeocoderClient,
    EvidenceLedger,
    GoogleStreetViewClient,
    HostRateLimiter,
    MapillaryClient,
    NPPESClient,
    OverpassClient,
    ResilientHTTP,
    RetryPolicy,
    SAMEntityClient,
    SocrataClient,
    USASpendingClient,
)
from .enrichment import CrossReferencer, EnrichmentConfig, OperatorFormationIndex
from .dossier import DossierExporter, source_record_ids
from .ingestion import TargetCollector
from .leie_matcher import LEIEMatcher
from .licensing import LicenseLookup, LicenseSource
from .math_engine import CapacityEngine
from .models import AwardeeEntity, EnrichmentData, FraudRiskDossier, RuleStatus, ScanContext, ScoreBreakdown, utcnow
from .scorer import PHYSICAL_SITE_MISMATCH, DeterministicScorer, ScoringConfig, ScoringContext
from .synthesizer import LLMBackend, Synthesizer
from .verticals import Vertical
from .vision_verifier import VisionModel, VisionVerifier

log = logging.getLogger(__name__)

ProgressFn = Callable[[str, float], None]


# --------------------------------------------------------------------------- #
# DOJ Health Care Fraud Strike Force targeting presets
# --------------------------------------------------------------------------- #
# Operator presets modelled on the regions where DOJ's Health Care Fraud Strike Forces
# operate. They are targeting conveniences, not official DOJ designations. Each lists
# the federal judicial districts and the counties (state + 3-digit county FIPS) whose
# recipients are pulled from USASpending. A whole-state entry covers states that form
# a single judicial district (AZ, NV, NJ).

DEFAULT_TARGET_NAICS = ["624410", "624210", "621610", "623110", "624120"]


@dataclass(frozen=True)
class FocusCounty:
    state: str
    county_fips: str  # 3-digit county FIPS
    name: str
    district: str

    @property
    def geoid(self) -> str:
        return f"{STATE_FIPS[self.state]}{self.county_fips}"


@dataclass(frozen=True)
class StrikeForcePreset:
    key: str
    label: str
    metros: tuple[str, ...]
    districts: tuple[str, ...]
    counties: tuple[FocusCounty, ...] = ()
    whole_states: dict[str, str] = field(default_factory=dict)  # state -> single district covering it
    naics_codes: tuple[str, ...] = tuple(DEFAULT_TARGET_NAICS)

    @property
    def states(self) -> list[str]:
        return sorted({c.state for c in self.counties} | set(self.whole_states))

    def location_filters(self) -> list[dict[str, str]]:
        """USASpending recipient_locations objects for this preset."""
        locs = [{"country": "USA", "state": c.state, "county": c.county_fips} for c in self.counties]
        return locs + [{"country": "USA", "state": st} for st in sorted(self.whole_states)]


STATE_FIPS = {"FL": "12", "TX": "48", "CA": "06", "AZ": "04", "NV": "32", "MI": "26", "IL": "17", "NY": "36",
              "NJ": "34", "PA": "42", "LA": "22", "MS": "28"}

_SDFL, _MDFL = "Southern District of Florida", "Middle District of Florida"
_SDTX, _NDTX = "Southern District of Texas", "Northern District of Texas"
_CDCA, _NDCA = "Central District of California", "Northern District of California"
_EDMI, _NDIL = "Eastern District of Michigan", "Northern District of Illinois"
_EDNY, _EDPA = "Eastern District of New York", "Eastern District of Pennsylvania"
_EDLA, _MDLA, _SDMS = "Eastern District of Louisiana", "Middle District of Louisiana", "Southern District of Mississippi"

STRIKE_FORCE_PRESETS: dict[str, StrikeForcePreset] = {
    "florida": StrikeForcePreset(
        key="florida", label="Florida Strike Force", metros=("Miami-Dade", "Broward", "Tampa Bay"), districts=(_SDFL, _MDFL),
        counties=(FocusCounty("FL", "086", "Miami-Dade", _SDFL), FocusCounty("FL", "011", "Broward", _SDFL),
                  FocusCounty("FL", "099", "Palm Beach", _SDFL), FocusCounty("FL", "057", "Hillsborough", _MDFL),
                  FocusCounty("FL", "103", "Pinellas", _MDFL), FocusCounty("FL", "101", "Pasco", _MDFL)),
    ),
    "texas": StrikeForcePreset(
        key="texas", label="Texas Strike Force", metros=("Houston", "Dallas-Fort Worth", "McAllen"), districts=(_SDTX, _NDTX),
        counties=(FocusCounty("TX", "201", "Harris", _SDTX), FocusCounty("TX", "157", "Fort Bend", _SDTX),
                  FocusCounty("TX", "215", "Hidalgo", _SDTX), FocusCounty("TX", "061", "Cameron", _SDTX),
                  FocusCounty("TX", "113", "Dallas", _NDTX), FocusCounty("TX", "439", "Tarrant", _NDTX),
                  FocusCounty("TX", "085", "Collin", _NDTX), FocusCounty("TX", "121", "Denton", _NDTX)),
    ),
    "west_coast": StrikeForcePreset(
        key="west_coast", label="West Coast Strike Force", metros=("Los Angeles", "Orange County", "Bay Area", "Phoenix", "Las Vegas"),
        districts=(_CDCA, _NDCA, "District of Arizona", "District of Nevada"),
        counties=(FocusCounty("CA", "037", "Los Angeles", _CDCA), FocusCounty("CA", "059", "Orange", _CDCA),
                  FocusCounty("CA", "065", "Riverside", _CDCA), FocusCounty("CA", "071", "San Bernardino", _CDCA),
                  FocusCounty("CA", "075", "San Francisco", _NDCA), FocusCounty("CA", "001", "Alameda", _NDCA),
                  FocusCounty("CA", "085", "Santa Clara", _NDCA), FocusCounty("CA", "081", "San Mateo", _NDCA),
                  FocusCounty("CA", "013", "Contra Costa", _NDCA)),
        whole_states={"AZ": "District of Arizona", "NV": "District of Nevada"},
    ),
    "midwest": StrikeForcePreset(
        key="midwest", label="Midwest Strike Force", metros=("Detroit", "Chicago"), districts=(_EDMI, _NDIL),
        counties=(FocusCounty("MI", "163", "Wayne", _EDMI), FocusCounty("MI", "125", "Oakland", _EDMI),
                  FocusCounty("MI", "099", "Macomb", _EDMI), FocusCounty("IL", "031", "Cook", _NDIL),
                  FocusCounty("IL", "043", "DuPage", _NDIL), FocusCounty("IL", "097", "Lake", _NDIL),
                  FocusCounty("IL", "197", "Will", _NDIL)),
    ),
    "northeast": StrikeForcePreset(
        key="northeast", label="Northeast Strike Force", metros=("Brooklyn", "Queens", "Newark", "Philadelphia"),
        districts=(_EDNY, "District of New Jersey", _EDPA),
        counties=(FocusCounty("NY", "047", "Kings (Brooklyn)", _EDNY), FocusCounty("NY", "081", "Queens", _EDNY),
                  FocusCounty("NY", "085", "Richmond (Staten Island)", _EDNY), FocusCounty("NY", "059", "Nassau", _EDNY),
                  FocusCounty("NY", "103", "Suffolk", _EDNY), FocusCounty("PA", "101", "Philadelphia", _EDPA),
                  FocusCounty("PA", "091", "Montgomery", _EDPA), FocusCounty("PA", "045", "Delaware", _EDPA),
                  FocusCounty("PA", "017", "Bucks", _EDPA)),
        whole_states={"NJ": "District of New Jersey"},
    ),
    "gulf_coast": StrikeForcePreset(
        key="gulf_coast", label="Gulf Coast Strike Force", metros=("New Orleans", "Baton Rouge", "Biloxi"),
        districts=(_EDLA, _MDLA, _SDMS),
        counties=(FocusCounty("LA", "071", "Orleans Parish", _EDLA), FocusCounty("LA", "051", "Jefferson Parish", _EDLA),
                  FocusCounty("LA", "103", "St. Tammany Parish", _EDLA),
                  # Baton Rouge (East Baton Rouge Parish) lies in the Middle District of Louisiana, not the Eastern.
                  FocusCounty("LA", "033", "East Baton Rouge Parish", _MDLA),
                  FocusCounty("MS", "047", "Harrison (Biloxi)", _SDMS), FocusCounty("MS", "045", "Hancock", _SDMS),
                  FocusCounty("MS", "059", "Jackson", _SDMS)),
    ),
}
CUSTOM_PRESET_KEY = "custom"


def us_attorney_office(district: str) -> str:
    return f"U.S. Attorney's Office, {district}"


def assign_strike_force(entity: AwardeeEntity, enrichment: EnrichmentData, preset: StrikeForcePreset | None) -> None:
    """Record which preset district (if any) a recipient falls in, from Census county FIPS or the address of record."""
    if preset is None:
        return
    from .models import StrikeForceTarget

    geo = enrichment.geocode
    state = (entity.address.state or "").upper()
    county = None
    basis = ""
    if geo.county_fips:
        county = next((c for c in preset.counties if c.geoid == geo.county_fips), None)
        basis = f"Census county FIPS {geo.county_fips}"
    if county is None and entity.address.county:
        name = entity.address.county.upper().replace(" COUNTY", "").replace(" PARISH", "")
        county = next((c for c in preset.counties if c.state == state and c.name.upper().split(" (")[0].replace(" PARISH", "") == name), None)
        if county and not basis:
            basis = f"USASpending county name '{entity.address.county}'"
    district = county.district if county else preset.whole_states.get(state)
    if district and not county:
        basis = f"state {state} is a single judicial district"
    enrichment.strike_force = StrikeForceTarget(
        hub_key=preset.key, hub_label=preset.label, in_focus_area=bool(district), district=district,
        us_attorney_office=us_attorney_office(district) if district else None,
        county_name=county.name if county else (geo.county_name or entity.address.county),
        county_fips=geo.county_fips,
        basis=basis if district else (f"{basis + ': ' if basis else ''}county not among the preset's focus counties; "
                                      "verify the district at uscourts.gov"),
    )


@dataclass
class VisionSettings:
    model: VisionModel
    mapillary_token: str | None = None
    google_api_key: str | None = None


@dataclass
class PipelineSettings:
    filters: AwardSearchFilters
    award_groups: list[str]
    max_awards: int = 200
    threshold: int = 50
    output_dir: Path = Path("./dossiers")
    evidence_dir: Path | None = None
    vertical: Vertical | None = None
    detail_awards_per_entity: int = 3
    enrichment: EnrichmentConfig = field(default_factory=EnrichmentConfig)
    scoring: ScoringConfig = field(default_factory=ScoringConfig)
    sam_api_key: str | None = None
    formation_index: OperatorFormationIndex | None = None
    leie: LEIEMatcher | None = None
    license_sources: list[LicenseSource] = field(default_factory=list)
    capacity: CapacityEngine | None = field(default_factory=CapacityEngine)
    vision: VisionSettings | None = None
    overpass_url: str | None = None
    strike_force: StrikeForcePreset | None = None
    nationwide: bool = False
    timeout: float = 30.0
    max_attempts: int = 5


@dataclass
class EntityResult:
    entity: AwardeeEntity
    enrichment: EnrichmentData
    score: ScoreBreakdown
    dossier: FraudRiskDossier | None = None
    json_path: Path | None = None
    md_path: Path | None = None
    evidence_path: Path | None = None
    evidence_root_sha256: str | None = None


@dataclass
class ScanResult:
    scan: ScanContext
    results: list[EntityResult]
    manifest_path: Path
    ledger_path: Path
    failed_groups: dict[str, str] = field(default_factory=dict)
    context: ScoringContext | None = None
    awards_scanned: int = 0
    skipped: dict[str, str] = field(default_factory=dict)  # entity name -> why it was not scored

    @property
    def leads(self) -> list[EntityResult]:
        return [r for r in self.results if r.dossier is not None]


class Pipeline:
    def __init__(self, settings: PipelineSettings, backend: LLMBackend | None = None,
                 transport: httpx.BaseTransport | None = None, sleep=None, limiter: HostRateLimiter | None = None,
                 progress: ProgressFn | None = None, ledger_namespace: str = ""):
        self.settings = settings
        self.progress = progress or (lambda msg, frac: None)
        self.ledger = EvidenceLedger(namespace=ledger_namespace)
        http_kwargs: dict = {"sleep": sleep} if sleep else {}
        if limiter:
            http_kwargs["limiter"] = limiter
        self.http = ResilientHTTP(ledger=self.ledger, timeout=settings.timeout,
                                  retry=RetryPolicy(max_attempts=settings.max_attempts), transport=transport, **http_kwargs)
        self.collector = TargetCollector(USASpendingClient(self.http), settings.detail_awards_per_entity)
        vision = None
        if settings.vision:
            v = settings.vision
            vision = VisionVerifier(
                v.model,
                mapillary=MapillaryClient(self.http, v.mapillary_token) if v.mapillary_token else None,
                google=GoogleStreetViewClient(self.http, v.google_api_key) if v.google_api_key else None,
            )
        self.cross = CrossReferencer(
            nppes=NPPESClient(self.http),
            geocoder=CensusGeocoderClient(self.http),
            overpass=OverpassClient(self.http, settings.overpass_url),
            sam=SAMEntityClient(self.http, settings.sam_api_key) if settings.sam_api_key else None,
            formation_index=settings.formation_index,
            vertical=settings.vertical,
            config=settings.enrichment,
            leie=settings.leie,
            licenses=LicenseLookup(settings.license_sources, SocrataClient(self.http)) if settings.license_sources else None,
            capacity=settings.capacity,
            vision=vision,
            ledger=self.ledger,
        )
        self.scorer = DeterministicScorer(settings.scoring, source_lookup=self.ledger.get)
        self.synth = Synthesizer(backend, high_priority_threshold=settings.scoring.high_priority_threshold)
        self.exporter = DossierExporter(self.synth, settings.output_dir, settings.evidence_dir)

    def close(self) -> None:
        self.http.close()

    def scan_context(self) -> ScanContext:
        from . import __version__

        f, s = self.settings.filters, self.settings
        return ScanContext(
            state=f.state, zip_codes=f.zip_codes, vertical=s.vertical.key if s.vertical else None,
            naics_codes=f.naics_codes, psc_codes=f.psc_codes, program_numbers=f.program_numbers,
            award_groups=s.award_groups, min_award=f.min_award, max_award=f.max_award,
            start_date=f.start_date, end_date=f.end_date, max_awards=s.max_awards, threshold=s.threshold,
            scoring_profile=s.scoring.profile, strike_force=s.strike_force.key if s.strike_force else None,
            nationwide=s.nationwide, tool_version=__version__,
        )

    source_ids = staticmethod(source_record_ids)

    def enrich_entity(self, entity: AwardeeEntity) -> EnrichmentData:
        """Cross-reference one (hydrated) recipient and place it relative to the Strike Force preset."""
        enrichment = self.cross.enrich(entity)
        assign_strike_force(entity, enrichment, self.settings.strike_force)
        return enrichment

    def run(self) -> ScanResult:
        s = self.settings
        scan = self.scan_context()

        self.progress("Querying USASpending.gov awards", 0.02)
        collected = self.collector.collect(s.filters, s.award_groups, s.max_awards)
        awards_scanned = sum(len(e.awards) for e in collected)
        # Scoring needs only the award financials from the search. Profile/detail/enrichment gaps degrade
        # individual rules to "insufficient data"; a recipient is skipped only if it has no positive award amount.
        entities = [e for e in collected if e.has_award_financials]
        self._skipped = {e.name: "no award with a positive obligated amount" for e in collected if not e.has_award_financials}

        pairs = []
        for i, entity in enumerate(entities, 1):
            self.progress(f"Cross-referencing {entity.name} ({i}/{len(entities)})", 0.10 + 0.65 * i / max(1, len(entities)))
            pairs.append((entity, self.enrich_entity(entity)))

        self.progress("Building entity graph and scoring", 0.78)
        context = ScoringContext.build(pairs, s.scoring.affiliation_name_similarity, s.vertical.key if s.vertical else None)
        results = [EntityResult(e, d, self.scorer.score(e, d, context)) for e, d in pairs]

        if self.cross.vision:
            site_weight = s.scoring.weights.get(PHYSICAL_SITE_MISMATCH, 0)
            candidates = [r for r in results if site_weight and r.score.score + site_weight >= s.threshold
                          and next(ev for ev in r.score.evaluations if ev.rule_id == PHYSICAL_SITE_MISMATCH).status
                          is RuleStatus.INSUFFICIENT_DATA]
            for i, r in enumerate(candidates, 1):
                self.progress(f"Street-level site check {r.entity.name} ({i}/{len(candidates)})", 0.80 + 0.08 * i / len(candidates))
                self.cross.verify_site(r.entity, r.enrichment)
                r.score = self.scorer.score(r.entity, r.enrichment, context)

        results.sort(key=lambda r: (-r.score.score, -r.entity.total_awarded))

        self.progress("Archiving evidence and writing dossiers", 0.90)
        vertical_caveats = s.vertical.caveats if s.vertical else []
        for r in results:
            if r.score.score < s.threshold:
                continue
            out = self.exporter.export(r.entity, r.enrichment, r.score, context, self.ledger, scan, vertical_caveats)
            r.dossier, r.json_path, r.md_path = out.dossier, out.json_path, out.md_path
            r.evidence_path, r.evidence_root_sha256 = out.evidence_dir, out.root_sha256
            log.info("lead: %s (score %d) -> %s", r.entity.name, r.score.score, r.md_path)

        manifest_path, ledger_path = self._write_manifest(scan, results)
        self.progress("Scan complete", 1.0)
        return ScanResult(scan, results, manifest_path, ledger_path, failed_groups=dict(self.collector.failed_groups),
                          context=context, awards_scanned=awards_scanned, skipped=self._skipped)

    def _write_manifest(self, scan: ScanContext, results: list[EntityResult]) -> tuple[Path, Path]:
        out = self.settings.output_dir
        out.mkdir(parents=True, exist_ok=True)
        stamp = f"{utcnow():%Y%m%dT%H%M%S%fZ}"
        ledger_path = out / f"source_ledger_{stamp}.jsonl"
        with ledger_path.open("w", encoding="utf-8") as fh:
            for rec in self.ledger.all():
                fh.write(rec.model_dump_json() + "\n")
        manifest = {
            "scan": json.loads(scan.model_dump_json()),
            "completed_at": utcnow().isoformat(),
            "scoring_config": json.loads(self.settings.scoring.model_dump_json()),
            "entities_screened": len(results),
            "leads_at_or_above_threshold": sum(1 for r in results if r.dossier),
            "skipped": getattr(self, "_skipped", {}),
            "entities_with_data_gaps": {r.entity.name: r.entity.data_gaps for r in results if r.entity.data_gaps},
            "source_records": len(self.ledger),
            "source_ledger": ledger_path.name,
            "failed_award_searches": self.collector.failed_groups,
            "results": [
                {
                    "entity_key": r.entity.entity_key, "name": r.entity.name, "uei": r.entity.uei,
                    "address": r.entity.address.one_line, "total_awarded": r.entity.total_awarded,
                    "score": r.score.score, "raw_total": r.score.raw_total,
                    "rule_status": {ev.rule_id: ev.status.value for ev in r.score.evaluations},
                    "dossier_json": r.json_path.name if r.json_path else None,
                    "dossier_md": r.md_path.name if r.md_path else None,
                    "evidence_dir": str(r.evidence_path) if r.evidence_path else None,
                    "evidence_root_sha256": r.evidence_root_sha256,
                    "strike_force_district": r.enrichment.strike_force.district if r.enrichment.strike_force else None,
                }
                for r in results
            ],
            "notice": "Lead-generation output for human review only. Nothing has been submitted to any agency.",
        }
        manifest_path = out / f"scan_manifest_{stamp}.json"
        manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        return manifest_path, ledger_path
