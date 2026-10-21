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
from .evidence import EvidenceVault
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


@dataclass
class ScanResult:
    scan: ScanContext
    results: list[EntityResult]
    manifest_path: Path
    ledger_path: Path
    failed_groups: dict[str, str] = field(default_factory=dict)
    context: ScoringContext | None = None
    awards_scanned: int = 0

    @property
    def leads(self) -> list[EntityResult]:
        return [r for r in self.results if r.dossier is not None]


class Pipeline:
    def __init__(self, settings: PipelineSettings, backend: LLMBackend | None = None,
                 transport: httpx.BaseTransport | None = None, sleep=None, limiter: HostRateLimiter | None = None,
                 progress: ProgressFn | None = None):
        self.settings = settings
        self.progress = progress or (lambda msg, frac: None)
        self.ledger = EvidenceLedger()
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
        self.vault = EvidenceVault(settings.evidence_dir or settings.output_dir / "evidence")

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
            scoring_profile=s.scoring.profile, tool_version=__version__,
        )

    @staticmethod
    def source_ids(entity: AwardeeEntity, enrichment: EnrichmentData, score: ScoreBreakdown) -> set[str]:
        ids = set(entity.source_record_ids) | set(entity.formation_source_record_ids)
        for a in entity.awards:
            ids |= set(a.source_record_ids)
        for o in entity.officers:
            ids |= set(o.source_record_ids)
        for part in (enrichment.npi, enrichment.geocode, enrichment.land_use, enrichment.leie, enrichment.license):
            ids |= set(part.source_record_ids)
        if enrichment.license.best_match:
            ids |= set(enrichment.license.best_match.source_record_ids)
        if enrichment.vision.imagery:
            ids |= set(enrichment.vision.imagery.source_record_ids)
        for ev in score.evaluations:
            ids |= {item.source_record_id for item in ev.evidence if item.source_record_id}
        return ids

    def run(self) -> ScanResult:
        s = self.settings
        scan = self.scan_context()

        self.progress("Querying USASpending.gov awards", 0.02)
        entities = self.collector.collect(s.filters, s.award_groups, s.max_awards)
        awards_scanned = sum(len(e.awards) for e in entities)

        pairs = []
        for i, entity in enumerate(entities, 1):
            self.progress(f"Cross-referencing {entity.name} ({i}/{len(entities)})", 0.10 + 0.65 * i / max(1, len(entities)))
            pairs.append((entity, self.cross.enrich(entity)))

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
            ids = self.source_ids(r.entity, r.enrichment, r.score)
            dossier = self.synth.build_dossier(r.entity, r.enrichment, r.score, context, self.ledger.subset(ids), scan, vertical_caveats)
            dossier.evidence_manifest = self.vault.archive(dossier.dossier_id, ids, self.ledger)
            dossier.evidence_dir = str(self.vault.lead_dir(dossier.dossier_id))
            r.dossier = dossier
            r.json_path, r.md_path = self.synth.write(dossier, s.output_dir)
            self.vault.finalize(dossier.dossier_id, {"dossier.json": r.json_path.read_bytes(), "dossier.md": r.md_path.read_bytes()})
            r.evidence_path = self.vault.lead_dir(dossier.dossier_id)
            log.info("lead: %s (score %d) -> %s", r.entity.name, r.score.score, r.md_path)

        manifest_path, ledger_path = self._write_manifest(scan, results)
        self.progress("Scan complete", 1.0)
        return ScanResult(scan, results, manifest_path, ledger_path, failed_groups=dict(self.collector.failed_groups),
                          context=context, awards_scanned=awards_scanned)

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
                }
                for r in results
            ],
            "notice": "Lead-generation output for human review only. Nothing has been submitted to any agency.",
        }
        manifest_path = out / f"scan_manifest_{stamp}.json"
        manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        return manifest_path, ledger_path
