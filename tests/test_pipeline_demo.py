import json

import pytest

from scrimmy.demo import demo_settings, demo_transport
from scrimmy.evidence import EvidenceVault
from scrimmy.models import FraudRiskDossier, RuleStatus
from scrimmy.pipeline import Pipeline
from scrimmy.synthesizer import LanguageGuard

EXPECTED = {
    "registry": {
        "NORTHFIELD EXAMPLE HOME HEALTH LLC": 90,   # no NPI 30 + residential 25 + newly formed 20 + serial officer 15
        "SUMMIT EXAMPLE WELLNESS LLC": 75,          # no NPI 30 + CMRA 25 + co-location 20
        "LAKEVIEW EXAMPLE PERSONAL CARE LLC": 55,   # LEIE strong match 40 + serial officer 15
        "RIVERBEND EXAMPLE CARE SERVICES INC": 45,  # CMRA 25 + co-location 20
        "PINECREST EXAMPLE HOME CARE LLC": 40,      # no NPI 30 + address not geocodable 10
        "SUNRISE EXAMPLE LEARNING CENTER LLC": 20,  # newly formed 20 (capacity rule unscored in this profile)
        "CEDAR EXAMPLE HEALTH PARTNERS": 20,        # co-location 20
        "EXAMPLE REGIONAL MEDICAL CENTER INC": 0,
    },
    "capacity": {
        "SUNRISE EXAMPLE LEARNING CENTER LLC": 55,  # capacity ratio 40 + newly formed 15
        "SUMMIT EXAMPLE WELLNESS LLC": 40,
        "NORTHFIELD EXAMPLE HOME HEALTH LLC": 35,
        "LAKEVIEW EXAMPLE PERSONAL CARE LLC": 35,
        "RIVERBEND EXAMPLE CARE SERVICES INC": 20,
        "PINECREST EXAMPLE HOME CARE LLC": 20,
        "CEDAR EXAMPLE HEALTH PARTNERS": 20,
        "EXAMPLE REGIONAL MEDICAL CENTER INC": 0,
    },
}


@pytest.mark.parametrize("profile", ["registry", "capacity"])
def test_demo_end_to_end(tmp_path, no_wait_limiter, profile):
    pipe = Pipeline(demo_settings(tmp_path, 50, profile), transport=demo_transport(), sleep=lambda s: None, limiter=no_wait_limiter)
    result = pipe.run()
    pipe.close()

    assert {r.entity.name: r.score.score for r in result.results} == EXPECTED[profile]
    assert {r.entity.name for r in result.leads} == {k for k, v in EXPECTED[profile].items() if v >= 50}

    for lead in result.leads:
        dossier = FraudRiskDossier.model_validate_json(lead.json_path.read_text())
        source_ids = {s.record_id for s in dossier.sources}
        for flag in dossier.score.flags:
            for ev in flag.evidence:
                if ev.source_record_id:
                    assert ev.source_record_id in source_ids
                    assert ev.source_url and ev.retrieved_at
        assert all(s.response_sha256 for s in dossier.sources)
        assert dossier.score.raw_total == sum(e.points_awarded for e in dossier.score.evaluations if e.status is RuleStatus.TRIGGERED)
        assert dossier.track is not None and dossier.network.dot.startswith("graph")
        md = lead.md_path.read_text()
        for heading in ("Executive Summary & Lead Classification", "Entity Identity & Federal Award Summary",
                        "Flagged Discrepancies & Heuristic Breakdown", "Disbursement-to-Capacity Arithmetic",
                        "Physical Site Verification", "Exclusion Screening", "Network Summary",
                        "Relevant Statutory Frameworks", "Ground-Truthing Field Checklist", "Chain of Custody"):
            assert heading in md
        assert LanguageGuard().violations(dossier.executive_summary) == []
        # Evidence vault: write-once raw payloads, every file covered by SHA256SUMS
        assert EvidenceVault.verify(lead.evidence_path) == []
        api_files = list((lead.evidence_path / "api").iterdir())
        assert api_files and len(dossier.evidence_manifest) == len(api_files) + 1  # + ledger.jsonl
        assert (lead.evidence_path / "dossier.md").read_bytes() == lead.md_path.read_bytes()

    manifest = json.loads(result.manifest_path.read_text())
    assert manifest["entities_screened"] == 8
    assert len(result.ledger_path.read_text().splitlines()) == manifest["source_records"]


def test_capacity_math_in_dossier(tmp_path, no_wait_limiter):
    pipe = Pipeline(demo_settings(tmp_path, 50, "capacity"), transport=demo_transport(), sleep=lambda s: None, limiter=no_wait_limiter)
    lead = next(r for r in pipe.run().leads if r.entity.name.startswith("SUNRISE"))
    cap = lead.enrichment.capacity
    # $1,200,000 / ($8.40 x 250 days) = 571.43 per day vs licensed capacity 40 -> 14.286x
    assert cap.estimated_daily_beneficiaries == 571.43 and cap.licensed_capacity == 40 and cap.capacity_ratio == 14.286
    assert lead.enrichment.license.best_match.source.startswith("socrata:")


def test_evidence_tampering_is_detected(tmp_path, no_wait_limiter):
    pipe = Pipeline(demo_settings(tmp_path, 50), transport=demo_transport(), sleep=lambda s: None, limiter=no_wait_limiter)
    lead = pipe.run().leads[0]
    victim = next((lead.evidence_path / "api").iterdir())
    victim.write_bytes(victim.read_bytes() + b" ")
    assert EvidenceVault.verify(lead.evidence_path) == [f"hash mismatch: api/{victim.name}"]
