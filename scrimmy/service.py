"""Stateless scan operations for serverless hosting (e.g. the Vercel function in ``api/index.py``).

Serverless functions cannot hold a long-running scan, a WebSocket or files between
requests. So a scan is split into short, independent steps, and the browser keeps
the state between them:

    search      USASpending award search -> unhydrated entities
    enrich      one entity: recipient profile, award details, NPPES, Census, OSM, LEIE, licensing
    score       all entities -> deterministic scores (+ which leads merit a vision check)
    site-check  one entity: street-level imagery + vision model (optional)
    dossier     one lead -> dossier JSON + Markdown
    evidence    one lead -> evidence archive (.zip with SHA256SUMS)

Each step returns the SourceRecords it created, together with the raw response bytes,
so the evidence archive built at the end is byte-for-byte what the APIs returned.
"""

from __future__ import annotations

import base64
import io
import logging
import os
import secrets
import tempfile
import time
import zipfile
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx
from pydantic import BaseModel, Field

from .clients import EvidenceLedger, HostRateLimiter, ResilientHTTP
from .evidence import EvidenceVault
from .leie_matcher import LEIE_DOWNLOAD_URL, LEIEMatcher
from .licensing import LicenseSource
from .models import AwardeeEntity, EnrichmentData, FraudRiskDossier, RuleStatus, ScoreBreakdown, SourceRecord
from .options import ScanOptions
from .pipeline import Pipeline
from .scorer import PHYSICAL_SITE_MISMATCH, PROFILES, DeterministicScorer, ScoringContext
from .synthesizer import Synthesizer, make_backend
from .verticals import PRIORITY_VERTICALS, VERTICALS

log = logging.getLogger(__name__)

# Tests replace these to run the service against the offline demo transport without throttling.
TRANSPORT_FACTORY: Callable[[], httpx.BaseTransport | None] = lambda: None  # noqa: E731
LIMITER_FACTORY: Callable[[], HostRateLimiter | None] = lambda: None  # noqa: E731

LEIE_CACHE = Path(os.environ.get("SCRIMMY_LEIE_CACHE", Path(tempfile.gettempdir()) / "scrimmy_LEIE_UPDATED.csv"))
LEIE_MAX_AGE_S = 7 * 24 * 3600
_leie: tuple[float, LEIEMatcher] | None = None

MAX_AWARDS_LIMIT = 300


class ServiceError(Exception):
    pass


class WebScanRequest(BaseModel):
    """The scan settings the browser sends with every step."""

    state: str | None = None
    zip_codes: list[str] = Field(default_factory=list)
    verticals: list[str] = Field(default_factory=lambda: list(PRIORITY_VERTICALS))
    min_award: float | None = 250_000.0
    fiscal_year: int | None = None
    max_awards: int = Field(100, ge=1, le=MAX_AWARDS_LIMIT)
    threshold: int = Field(50, ge=0, le=100)
    scoring_profile: str = "registry"
    use_osm: bool = True
    leie: bool = False
    vision: bool = False
    llm_provider: str = "template"
    unit_rates: dict[str, float] = Field(default_factory=dict)
    license_sources: list[dict[str, Any]] = Field(default_factory=list, description="Socrata sources only (no server-side files).")

    def to_options(self) -> ScanOptions:
        unknown = [v for v in self.verticals if v not in VERTICALS]
        if unknown:
            raise ServiceError(f"unknown vertical(s): {', '.join(unknown)}")
        if self.scoring_profile not in PROFILES:
            raise ServiceError(f"unknown scoring profile {self.scoring_profile!r}")
        one = self.verticals[0] if len(self.verticals) == 1 else None
        opts = ScanOptions(
            state=self.state, zip_codes=self.zip_codes, vertical=one,
            naics=[] if one else sorted({c for k in self.verticals for c in VERTICALS[k].naics_codes}),
            programs=[] if one else sorted({c for k in self.verticals for c in VERTICALS[k].program_numbers}),
            min_award=self.min_award or None, fiscal_year=self.fiscal_year, max_awards=self.max_awards,
            threshold=self.threshold, scoring_profile=self.scoring_profile, skip_osm=not self.use_osm,
            unit_rates=self.unit_rates, vision=self.vision, output_dir=Path(tempfile.gettempdir()) / "scrimmy_out",
        )
        errors = opts.validate()
        if errors:
            raise ServiceError("; ".join(errors))
        return opts


class RecordBundle(BaseModel):
    record: SourceRecord
    raw_b64: str | None = None
    content_type: str | None = None


class Item(BaseModel):
    entity: AwardeeEntity
    enrichment: EnrichmentData = Field(default_factory=EnrichmentData)


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def load_leie() -> LEIEMatcher:
    """Download the LEIE once per warm instance (cached in /tmp for up to 7 days)."""
    global _leie
    fresh = LEIE_CACHE.exists() and time.time() - LEIE_CACHE.stat().st_mtime < LEIE_MAX_AGE_S
    if not fresh:
        http = ResilientHTTP(ledger=EvidenceLedger(), timeout=50)
        try:
            data, _ = http.request_bytes("hhs_oig_leie", "GET", LEIE_DOWNLOAD_URL)
        finally:
            http.close()
        LEIEMatcher.from_bytes(data)  # validate before caching
        LEIE_CACHE.write_bytes(data)
        _leie = None
    mtime = LEIE_CACHE.stat().st_mtime
    if _leie is None or _leie[0] != mtime:
        _leie = (mtime, LEIEMatcher.from_csv(LEIE_CACHE))
        _leie[1].path = LEIE_DOWNLOAD_URL
    return _leie[1]


def _pipeline(req: WebScanRequest, need_leie: bool = False) -> Pipeline:
    settings = req.to_options().to_settings()
    if need_leie and req.leie:
        settings.leie = load_leie()
    if req.license_sources:
        srcs = []
        for raw in req.license_sources:
            if raw.get("kind", "socrata") != "socrata":
                raise ServiceError("only 'socrata' licensing sources are supported in the hosted app")
            srcs.append(LicenseSource(key=raw["key"], kind="socrata", states=raw.get("states", []), verticals=raw.get("verticals", []),
                                      domain=raw["domain"], dataset_id=raw["dataset_id"], fields=raw.get("fields", {}), note=raw.get("note")))
        settings.license_sources = srcs
    return Pipeline(settings, transport=TRANSPORT_FACTORY(), limiter=LIMITER_FACTORY(), ledger_namespace=secrets.token_hex(3))


def _bundles(ledger: EvidenceLedger) -> list[dict[str, Any]]:
    out = []
    for rec in ledger.all():
        raw = ledger.raw_body(rec.record_id)
        out.append({"record": rec.model_dump(mode="json"), "raw_b64": base64.b64encode(raw[0]).decode() if raw else None,
                    "content_type": raw[1] if raw else None})
    return out


def _ledger_from(bundles: list[RecordBundle]) -> EvidenceLedger:
    ledger = EvidenceLedger()
    for b in bundles:
        ledger.add(b.record, base64.b64decode(b.raw_b64) if b.raw_b64 else None, b.content_type or "application/json")
    return ledger


def _context(req: WebScanRequest, items: list[Item]) -> tuple[ScoringContext, DeterministicScorer, list[tuple[AwardeeEntity, EnrichmentData]]]:
    settings = req.to_options().to_settings()
    pairs = [(i.entity, i.enrichment) for i in items]
    ctx = ScoringContext.build(pairs, settings.scoring.affiliation_name_similarity,
                               req.verticals[0] if len(req.verticals) == 1 else None)
    return ctx, DeterministicScorer(settings.scoring), pairs


# --------------------------------------------------------------------------- #
# Steps
# --------------------------------------------------------------------------- #


def search(req: WebScanRequest) -> dict[str, Any]:
    pipe = _pipeline(req)
    try:
        s = pipe.settings
        entities = pipe.collector.collect(s.filters, s.award_groups, s.max_awards, hydrate=False)
        return {
            "entities": [e.model_dump(mode="json") for e in entities],
            "awards_scanned": sum(len(e.awards) for e in entities),
            "failed_groups": pipe.collector.failed_groups,
            "all_failed": bool(pipe.collector.failed_groups) and len(pipe.collector.failed_groups) == len(s.award_groups),
            "records": _bundles(pipe.ledger),
        }
    finally:
        pipe.close()


def enrich(req: WebScanRequest, entity: AwardeeEntity) -> dict[str, Any]:
    pipe = _pipeline(req, need_leie=True)
    try:
        pipe.collector.hydrate(entity)
        enrichment = pipe.cross.enrich(entity)
        return {"entity": entity.model_dump(mode="json"), "enrichment": enrichment.model_dump(mode="json"), "records": _bundles(pipe.ledger)}
    finally:
        pipe.close()


def score(req: WebScanRequest, items: list[Item], records: list[SourceRecord]) -> dict[str, Any]:
    ctx, scorer, pairs = _context(req, items)
    by_id = {r.record_id: r for r in records}
    scorer._lookup = by_id.get
    site_weight = scorer.config.weights.get(PHYSICAL_SITE_MISMATCH, 0)
    out, candidates = [], []
    for e, d in pairs:
        bd = scorer.score(e, d, ctx)
        out.append({"entity_key": e.entity_key, "score": bd.model_dump(mode="json")})
        site = next(ev for ev in bd.evaluations if ev.rule_id == PHYSICAL_SITE_MISMATCH)
        if req.vision and site_weight and bd.score + site_weight >= req.threshold and site.status is RuleStatus.INSUFFICIENT_DATA \
                and d.geocode.matched and not d.vision.performed:
            candidates.append(e.entity_key)
    out.sort(key=lambda r: -r["score"]["score"])
    network = {}
    for e, _ in pairs:
        summary = ctx.graph.summary(e.entity_key)
        network[e.entity_key] = summary.model_dump(mode="json")
    return {"results": out, "site_check_candidates": candidates, "network": network}


def site_check(req: WebScanRequest, entity: AwardeeEntity, enrichment: EnrichmentData) -> dict[str, Any]:
    pipe = _pipeline(req)
    try:
        if not pipe.cross.vision:
            raise ServiceError("vision is not configured on this deployment")
        pipe.cross.verify_site(entity, enrichment)
        return {"enrichment": enrichment.model_dump(mode="json"), "records": _bundles(pipe.ledger)}
    finally:
        pipe.close()


def _build_dossier(req: WebScanRequest, items: list[Item], bundles: list[RecordBundle], entity_key: str) -> tuple[FraudRiskDossier, Synthesizer, EvidenceLedger]:
    ctx, scorer, pairs = _context(req, items)
    ledger = _ledger_from(bundles)
    scorer._lookup = ledger.get
    match = next(((e, d) for e, d in pairs if e.entity_key == entity_key), None)
    if match is None:
        raise ServiceError(f"entity {entity_key!r} not in the posted scan")
    e, d = match
    bd: ScoreBreakdown = scorer.score(e, d, ctx)
    synth = Synthesizer(make_backend(req.llm_provider), high_priority_threshold=req.threshold)
    ids = Pipeline.source_ids(e, d, bd)
    scan_pipe = Pipeline(req.to_options().to_settings(), transport=TRANSPORT_FACTORY())
    pipe_scan = scan_pipe.scan_context()
    scan_pipe.close()
    caveats = VERTICALS[req.verticals[0]].caveats if len(req.verticals) == 1 else []
    dossier = synth.build_dossier(e, d, bd, ctx, ledger.subset(ids), pipe_scan, caveats)
    return dossier, synth, ledger


def dossier(req: WebScanRequest, items: list[Item], bundles: list[RecordBundle], entity_key: str) -> dict[str, Any]:
    d, synth, _ = _build_dossier(req, items, bundles, entity_key)
    return {"dossier": d.model_dump(mode="json"), "markdown": synth.render_markdown(d)}


def evidence_zip(req: WebScanRequest, items: list[Item], bundles: list[RecordBundle], entity_key: str) -> tuple[str, bytes]:
    d, synth, ledger = _build_dossier(req, items, bundles, entity_key)
    ids = {r.record_id for r in d.sources}
    with tempfile.TemporaryDirectory() as tmp:
        vault = EvidenceVault(Path(tmp))
        d.evidence_manifest = vault.archive(d.dossier_id, ids, ledger)
        d.evidence_dir = d.dossier_id
        vault.finalize(d.dossier_id, {"dossier.json": d.model_dump_json(indent=2).encode(),
                                      "dossier.md": synth.render_markdown(d).encode()})
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            root = vault.lead_dir(d.dossier_id)
            for p in sorted(root.rglob("*")):
                if p.is_file():
                    zf.write(p, p.relative_to(root.parent).as_posix())
    return d.dossier_id, buf.getvalue()


def capabilities() -> dict[str, Any]:
    return {
        "leie": True,
        "vision": bool((os.environ.get("MAPILLARY_TOKEN") or os.environ.get("GOOGLE_MAPS_API_KEY"))
                       and (os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("OPENAI_API_KEY"))),
        "llm": [p for p, k in (("anthropic", "ANTHROPIC_API_KEY"), ("openai", "OPENAI_API_KEY")) if os.environ.get(k)],
        "verticals": {k: VERTICALS[k].label for k in VERTICALS},
        "priority_verticals": PRIORITY_VERTICALS,
        "profiles": list(PROFILES),
        "max_awards_limit": MAX_AWARDS_LIMIT,
        "access_token_required": bool(os.environ.get("SCRIMMY_ACCESS_TOKEN")),
    }
