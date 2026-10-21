import json

from scrimmy.demo import demo_settings, demo_transport
from scrimmy.models import FraudRiskDossier, RuleStatus
from scrimmy.pipeline import Pipeline
from scrimmy.synthesizer import LanguageGuard


def test_demo_end_to_end(tmp_path, no_wait_limiter):
    pipe = Pipeline(demo_settings(tmp_path), transport=demo_transport(), sleep=lambda s: None, limiter=no_wait_limiter)
    result = pipe.run()
    pipe.close()

    scores = {r.entity.name: r.score.score for r in result.results}
    assert scores == {
        "NORTHFIELD EXAMPLE HOME HEALTH LLC": 95,
        "SUMMIT EXAMPLE WELLNESS LLC": 70,
        "RIVERBEND EXAMPLE CARE SERVICES INC": 45,
        "CEDAR EXAMPLE HEALTH PARTNERS": 20,
        "LAKEVIEW EXAMPLE PERSONAL CARE LLC": 15,
        "EXAMPLE REGIONAL MEDICAL CENTER INC": 0,
    }
    assert {r.entity.name for r in result.leads} == {"NORTHFIELD EXAMPLE HOME HEALTH LLC", "SUMMIT EXAMPLE WELLNESS LLC"}

    for lead in result.leads:
        # JSON dossier round-trips through the strict schema
        dossier = FraudRiskDossier.model_validate_json(lead.json_path.read_text())
        source_ids = {s.record_id for s in dossier.sources}
        # Chain of custody: every cited source record is embedded with URL, timestamp and hash
        for flag in dossier.score.flags:
            for ev in flag.evidence:
                if ev.source_record_id:
                    assert ev.source_record_id in source_ids
                    assert ev.source_url and ev.retrieved_at
        assert all(s.response_sha256 and s.raw_snippet is not None for s in dossier.sources)
        # Score arithmetic is reproducible from the breakdown alone
        assert dossier.score.raw_total == sum(e.points_awarded for e in dossier.score.evaluations if e.status is RuleStatus.TRIGGERED)
        md = lead.md_path.read_text()
        for heading in ("Executive Summary", "Evidence Matrix", "Specific Inconsistencies", "Statutory Cross-References",
                        "Ground-Truthing Checklist", "Source Ledger"):
            assert heading in md
        assert LanguageGuard().violations(dossier.executive_summary) == []

    manifest = json.loads(result.manifest_path.read_text())
    assert manifest["entities_screened"] == 6 and manifest["leads_at_or_above_threshold"] == 2
    assert len(result.ledger_path.read_text().splitlines()) == manifest["source_records"]
