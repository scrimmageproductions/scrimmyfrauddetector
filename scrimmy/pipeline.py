"""Orchestrates the four stages: Ingestion -> Enrichment -> Scoring -> Synthesis."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path

import httpx

from .clients import (
    AwardSearchFilters,
    CensusGeocoderClient,
    EvidenceLedger,
    HostRateLimiter,
    NPPESClient,
    OverpassClient,
    ResilientHTTP,
    RetryPolicy,
    SAMEntityClient,
    USASpendingClient,
)
from .enrichment import CrossReferencer, EnrichmentConfig, OperatorFormationIndex
from .ingestion import TargetCollector
from .models import AwardeeEntity, EnrichmentData, FraudRiskDossier, ScanContext, ScoreBreakdown, utcnow
from .scorer import DeterministicScorer, ScoringConfig, ScoringContext
from .synthesizer import LLMBackend, Synthesizer
from .verticals import Vertical

log = logging.getLogger(__name__)


@dataclass
class PipelineSettings:
    filters: AwardSearchFilters
    award_groups: list[str]
    max_awards: int = 200
    threshold: int = 50
    output_dir: Path = Path("./dossiers")
    vertical: Vertical | None = None
    detail_awards_per_entity: int = 3
    enrichment: EnrichmentConfig = field(default_factory=EnrichmentConfig)
    scoring: ScoringConfig = field(default_factory=ScoringConfig)
    sam_api_key: str | None = None
    formation_index: OperatorFormationIndex | None = None
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


@dataclass
class ScanResult:
    scan: ScanContext
    results: list[EntityResult]
    manifest_path: Path
    ledger_path: Path
    failed_groups: dict[str, str] = field(default_factory=dict)

    @property
    def leads(self) -> list[EntityResult]:
        return [r for r in self.results if r.dossier is not None]


class Pipeline:
    def __init__(self, settings: PipelineSettings, backend: LLMBackend | None = None,
                 transport: httpx.BaseTransport | None = None, sleep=None, limiter: HostRateLimiter | None = None):
        self.settings = settings
        self.ledger = EvidenceLedger()
        http_kwargs: dict = {"sleep": sleep} if sleep else {}
        if limiter:
            http_kwargs["limiter"] = limiter
        self.http = ResilientHTTP(
            ledger=self.ledger, timeout=settings.timeout,
            retry=RetryPolicy(max_attempts=settings.max_attempts), transport=transport, **http_kwargs,
        )
        self.collector = TargetCollector(USASpendingClient(self.http), settings.detail_awards_per_entity)
        self.cross = CrossReferencer(
            nppes=NPPESClient(self.http),
            geocoder=CensusGeocoderClient(self.http),
            overpass=OverpassClient(self.http, settings.overpass_url),
            sam=SAMEntityClient(self.http, settings.sam_api_key) if settings.sam_api_key else None,
            formation_index=settings.formation_index,
            vertical=settings.vertical,
            config=settings.enrichment,
        )
        self.scorer = DeterministicScorer(settings.scoring, source_lookup=self.ledger.get)
        self.synth = Synthesizer(backend)

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
            tool_version=__version__,
        )

    @staticmethod
    def _source_ids(entity: AwardeeEntity, enrichment: EnrichmentData, score: ScoreBreakdown) -> set[str]:
        ids = set(entity.source_record_ids) | set(entity.formation_source_record_ids)
        for a in entity.awards:
            ids |= set(a.source_record_ids)
        for o in entity.officers:
            ids |= set(o.source_record_ids)
        ids |= set(enrichment.npi.source_record_ids) | set(enrichment.geocode.source_record_ids) | set(enrichment.land_use.source_record_ids)
        for ev in score.evaluations:
            ids |= {item.source_record_id for item in ev.evidence if item.source_record_id}
        return ids

    def run(self) -> ScanResult:
        s = self.settings
        scan = self.scan_context()

        log.info("Stage 1/4: collecting awards from USASpending (%s)", ", ".join(s.award_groups))
        entities = self.collector.collect(s.filters, s.award_groups, s.max_awards)

        log.info("Stage 2/4: enriching %d recipients (NPPES / Census / OSM)", len(entities))
        pairs = []
        for i, entity in enumerate(entities, 1):
            log.info("  [%d/%d] %s", i, len(entities), entity.name)
            pairs.append((entity, self.cross.enrich(entity)))

        log.info("Stage 3/4: deterministic scoring")
        context = ScoringContext.build(pairs, s.scoring.affiliation_name_similarity)
        results = [EntityResult(e, d, self.scorer.score(e, d, context)) for e, d in pairs]
        results.sort(key=lambda r: (-r.score.score, -r.entity.total_awarded))

        log.info("Stage 4/4: synthesizing dossiers for scores >= %d", s.threshold)
        vertical_caveats = s.vertical.caveats if s.vertical else []
        for r in results:
            if r.score.score < s.threshold:
                continue
            sources = self.ledger.subset(self._source_ids(r.entity, r.enrichment, r.score))
            r.dossier = self.synth.build_dossier(r.entity, r.enrichment, r.score, context, sources, scan, vertical_caveats)
            r.json_path, r.md_path = self.synth.write(r.dossier, s.output_dir)
            log.info("  lead: %s (score %d) -> %s", r.entity.name, r.score.score, r.md_path)

        return ScanResult(scan, results, *self._write_manifest(scan, results), failed_groups=dict(self.collector.failed_groups))

    def _write_manifest(self, scan: ScanContext, results: list[EntityResult]) -> tuple[Path, Path]:
        out = self.settings.output_dir
        out.mkdir(parents=True, exist_ok=True)
        stamp = f"{utcnow():%Y%m%dT%H%M%SZ}"
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
                    "entity_key": r.entity.entity_key,
                    "name": r.entity.name,
                    "uei": r.entity.uei,
                    "address": r.entity.address.one_line,
                    "total_awarded": r.entity.total_awarded,
                    "score": r.score.score,
                    "raw_total": r.score.raw_total,
                    "rule_status": {ev.rule_id: ev.status.value for ev in r.score.evaluations},
                    "dossier_json": r.json_path.name if r.json_path else None,
                    "dossier_md": r.md_path.name if r.md_path else None,
                }
                for r in results
            ],
            "notice": "Lead-generation output for human review only. Nothing has been submitted to any agency.",
        }
        manifest_path = out / f"scan_manifest_{stamp}.json"
        manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        return manifest_path, ledger_path
