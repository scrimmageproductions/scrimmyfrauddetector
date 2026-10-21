"""Evidence vault: write-once raw snapshots with SHA-256 integrity for each lead.

Layout of ``<evidence_root>/<lead_id>/``::

    api/<record_id>.json|.jpg|...   exact response bodies, byte-for-byte as received
    ledger.jsonl                    one SourceRecord per line (URL, request body, UTC timestamp, attempts, hash)
    provenance.json                 cryptographic provenance: for every API call the exact URL, request body,
                                    UTC timestamp, HTTP status, attempts, SHA-256 of the response as received,
                                    and the archived file's path, size and SHA-256
    dossier.json / dossier.md       the lead dossier (added after rendering)
    SHA256SUMS                      `sha256sum -c SHA256SUMS` verifies every file above

Files are opened in exclusive-create mode, so an existing artifact is never overwritten.
Re-running a scan produces a new lead id and directory.

Google Street View imagery is never written (Google Maps Platform terms). Its
SHA-256 and pano_id are kept in the dossier instead.
"""

from __future__ import annotations

import hashlib
import json
import logging
from pathlib import Path

from .clients import EvidenceLedger
from .models import EvidenceArtifact, utcnow

log = logging.getLogger(__name__)

NOT_ARCHIVABLE_SOURCES = {"google_street_view"}
_EXT = {"application/json": ".json", "image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp",
        "text/csv": ".csv", "text/plain": ".txt", "application/geo+json": ".json"}


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


class EvidenceVault:
    def __init__(self, root: Path):
        self.root = Path(root)

    def lead_dir(self, lead_id: str) -> Path:
        return self.root / lead_id

    @staticmethod
    def _write_once(path: Path, data: bytes) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("xb") as fh:
            fh.write(data)

    def archive(self, lead_id: str, record_ids: set[str], ledger: EvidenceLedger) -> list[EvidenceArtifact]:
        d = self.lead_dir(lead_id)
        d.mkdir(parents=True, exist_ok=False)
        artifacts: list[EvidenceArtifact] = []
        records = ledger.subset(record_ids)
        for rec in records:
            if rec.source in NOT_ARCHIVABLE_SOURCES and rec.method == "GET" and "metadata" not in rec.url:
                continue
            body = ledger.raw_body(rec.record_id)
            if body is None:
                continue
            data, ctype = body
            ext = _EXT.get(ctype, ".bin")
            kind = "image" if ctype.startswith("image/") else "dataset_extract" if rec.method == "FILE" else "api_response"
            rel = Path("api") / f"{rec.record_id}{ext}"
            self._write_once(d / rel, data)
            artifacts.append(EvidenceArtifact(path=rel.as_posix(), sha256=hashlib.sha256(data).hexdigest(), bytes=len(data),
                                              kind=kind, source_record_id=rec.record_id, source_url=rec.url, retrieved_at=rec.retrieved_at))
        ledger_bytes = "".join(r.model_dump_json() + "\n" for r in records).encode()
        self._write_once(d / "ledger.jsonl", ledger_bytes)
        artifacts.append(EvidenceArtifact(path="ledger.jsonl", sha256=hashlib.sha256(ledger_bytes).hexdigest(),
                                          bytes=len(ledger_bytes), kind="ledger"))
        by_record = {a.source_record_id: a for a in artifacts if a.source_record_id}
        provenance = {
            "lead_id": lead_id,
            "created_at": utcnow().isoformat(),
            "hash_algorithm": "SHA-256",
            "records": [
                {
                    "record_id": rec.record_id, "source": rec.source, "method": rec.method, "url": rec.url,
                    "request_body": rec.request_body, "retrieved_at": rec.retrieved_at.isoformat(), "http_status": rec.status_code,
                    "attempts": rec.attempts, "response_sha256": rec.response_sha256, "error": rec.error,
                    "archived_file": by_record[rec.record_id].path if rec.record_id in by_record else None,
                    "archived_file_sha256": by_record[rec.record_id].sha256 if rec.record_id in by_record else None,
                    "archived": rec.record_id in by_record,
                    "response_sha256_scope": ("full source dataset file; archived_file is the extract of matched rows"
                                              if rec.method == "FILE" else "exact response body; equals archived_file_sha256"),
                }
                for rec in records
            ],
            "artifacts": [json.loads(a.model_dump_json()) for a in artifacts],
        }
        self._write_once(d / "provenance.json", json.dumps(provenance, indent=2, default=str).encode())
        return artifacts

    def finalize(self, lead_id: str, extra_files: dict[str, bytes]) -> Path:
        """Write the rendered dossier files and the SHA256SUMS covering every file in the lead directory."""
        d = self.lead_dir(lead_id)
        for name, data in extra_files.items():
            self._write_once(d / name, data)
        lines = [f"{sha256_file(p)}  {p.relative_to(d).as_posix()}" for p in sorted(d.rglob("*")) if p.is_file() and p.name != "SHA256SUMS"]
        sums = d / "SHA256SUMS"
        self._write_once(sums, ("\n".join(lines) + "\n").encode())
        return sums

    @staticmethod
    def verify(lead_dir: Path) -> list[str]:
        """Return a list of problems (empty if every file matches SHA256SUMS)."""
        problems = []
        for line in (lead_dir / "SHA256SUMS").read_text().splitlines():
            digest, _, rel = line.partition("  ")
            p = lead_dir / rel
            if not p.exists():
                problems.append(f"missing: {rel}")
            elif sha256_file(p) != digest:
                problems.append(f"hash mismatch: {rel}")
        return problems
