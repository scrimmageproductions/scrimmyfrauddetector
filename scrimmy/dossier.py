"""Evidence provenance & dossier exporter.

One entry point for turning a scored lead into its deliverables, used by the CLI
pipeline, the Streamlit console and the hosted (Vercel) API:

    <output>/<lead_id>_<name>.json     machine-readable FraudRiskDossier
    <output>/<lead_id>_<name>.md       whistleblower / intake briefing
    <evidence>/<lead_id>/              write-once evidence archive:
        api/<record_id>.*              raw API responses, byte-for-byte as received
        ledger.jsonl                   SourceRecords (URL, request body, UTC timestamp, attempts, response SHA-256)
        provenance.json                cryptographic provenance of every call and archived file
        dossier.json, dossier.md       copies of the dossier
        SHA256SUMS                     `sha256sum -c SHA256SUMS` verifies every file above

Prose comes from ``synthesizer.Synthesizer`` (deterministic template, or a guarded
LLM for the summary only). Raw-byte archiving comes from ``evidence.EvidenceVault``.
This module wires them together and records the archive's root hash.
"""

from __future__ import annotations

import hashlib
import io
import tempfile
import zipfile
from dataclasses import dataclass
from pathlib import Path

from .clients import EvidenceLedger
from .evidence import EvidenceVault
from .models import AwardeeEntity, EnrichmentData, FraudRiskDossier, ScanContext, ScoreBreakdown
from .scorer import ScoringContext
from .synthesizer import Synthesizer


def source_record_ids(entity: AwardeeEntity, enrichment: EnrichmentData, score: ScoreBreakdown) -> set[str]:
    """Every SourceRecord a lead's dossier cites: its own lookups plus records cited as rule evidence."""
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


@dataclass
class ExportResult:
    dossier: FraudRiskDossier
    markdown: str
    json_path: Path
    md_path: Path
    evidence_dir: Path
    root_sha256: str  # SHA-256 of SHA256SUMS: one value that pins the whole archive


class DossierExporter:
    def __init__(self, synth: Synthesizer, output_dir: Path, evidence_root: Path | None = None):
        self.synth = synth
        self.output_dir = Path(output_dir)
        self.vault = EvidenceVault(evidence_root or self.output_dir / "evidence")

    def build(self, entity: AwardeeEntity, enrichment: EnrichmentData, score: ScoreBreakdown, context: ScoringContext,
              ledger: EvidenceLedger, scan: ScanContext, caveats: list[str] | None = None) -> FraudRiskDossier:
        ids = source_record_ids(entity, enrichment, score)
        return self.synth.build_dossier(entity, enrichment, score, context, ledger.subset(ids), scan, caveats or [])

    def export(self, entity: AwardeeEntity, enrichment: EnrichmentData, score: ScoreBreakdown, context: ScoringContext,
               ledger: EvidenceLedger, scan: ScanContext, caveats: list[str] | None = None) -> ExportResult:
        dossier = self.build(entity, enrichment, score, context, ledger, scan, caveats)
        ids = {r.record_id for r in dossier.sources}
        dossier.evidence_manifest = self.vault.archive(dossier.dossier_id, ids, ledger)
        dossier.evidence_dir = str(self.vault.lead_dir(dossier.dossier_id))
        json_path, md_path = self.synth.write(dossier, self.output_dir)
        markdown = md_path.read_text(encoding="utf-8")
        sums = self.vault.finalize(dossier.dossier_id, {"dossier.json": json_path.read_bytes(), "dossier.md": md_path.read_bytes()})
        return ExportResult(dossier, markdown, json_path, md_path, self.vault.lead_dir(dossier.dossier_id),
                            hashlib.sha256(sums.read_bytes()).hexdigest())

    @staticmethod
    def export_zip(synth: Synthesizer, entity: AwardeeEntity, enrichment: EnrichmentData, score: ScoreBreakdown,
                   context: ScoringContext, ledger: EvidenceLedger, scan: ScanContext,
                   caveats: list[str] | None = None) -> tuple[FraudRiskDossier, bytes]:
        """Export into a temporary directory and return (dossier, zip bytes). Used where the disk is ephemeral."""
        with tempfile.TemporaryDirectory() as tmp:
            res = DossierExporter(synth, Path(tmp) / "out", Path(tmp) / "evidence").export(
                entity, enrichment, score, context, ledger, scan, caveats)
            buf = io.BytesIO()
            with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
                for p in sorted(res.evidence_dir.rglob("*")):
                    if p.is_file():
                        zf.write(p, p.relative_to(res.evidence_dir.parent).as_posix())
            return res.dossier, buf.getvalue()
