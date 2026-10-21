from datetime import date

import pytest

from scrimmy.models import (
    Address, AwardeeEntity, AwardRecord, EnrichmentData, LandUseClass, LandUseResult, NPIMatchResult, NPIRecord, Officer, RuleStatus,
)
from scrimmy.normalize import normalize_address, normalize_entity_name, normalize_person_name
from scrimmy.scorer import (
    CO_LOCATED_SHELL_CLUSTER as RULE_COLOCATION,
    EXECUTIVE_SERIAL_REGISTRATION as RULE_EXEC_DUP,
    HIGH_DOLLAR_RESIDENTIAL_ZONE as RULE_RESIDENTIAL,
    NEWLY_FORMED_WINDFALL as RULE_NEWLY_FORMED,
    PHANTOM_HEALTHCARE_PROVIDER as RULE_PHANTOM_HEALTH,
    DeterministicScorer,
    ScoringConfig,
    ScoringContext,
)


def entity(key, name, line1="1 Example St", line2=None, amount=300_000.0, signed=date(2025, 3, 1), formed=None,
           officers=(), healthcare=False, uei=None, city="Lakeview"):
    return AwardeeEntity(
        entity_key=key, name=name, normalized_name=normalize_entity_name(name), uei=uei,
        address=normalize_address(Address(line1=line1, line2=line2, city=city, state="MN", zip5="55990")),
        awards=[AwardRecord(award_id=f"{key}-1", award_group="grants", amount=amount, date_signed=signed)],
        formation_date=formed, formation_date_source="test" if formed else None,
        officers=[Officer(name=o, normalized_name=normalize_person_name(o), role_source="test") for o in officers],
        is_healthcare=healthcare, healthcare_basis="test" if healthcare else None,
    )


def enr(cls=LandUseClass.COMMERCIAL, npi=None):
    d = EnrichmentData(land_use=LandUseResult(performed=True, classification=cls))
    if npi is not None:
        d.npi = npi
    return d


def run(pairs, target_key=None, config=None):
    ctx = ScoringContext.build(pairs)
    scorer = DeterministicScorer(config)
    e, d = next(p for p in pairs if target_key in (None, p[0].entity_key))
    return scorer.score(e, d, ctx)


def status(bd, rule):
    return next(ev for ev in bd.evaluations if ev.rule_id == rule).status


# R1 --------------------------------------------------------------------------

@pytest.mark.parametrize("formed,expected", [
    (date(2025, 1, 1), RuleStatus.TRIGGERED),       # 59 days before the 2025-03-01 award
    (date(2024, 11, 2), RuleStatus.TRIGGERED),      # 119 days: strictly below 120
    (date(2024, 11, 1), RuleStatus.NOT_TRIGGERED),  # 120 days: boundary, not triggered
    (date(2025, 6, 1), RuleStatus.NOT_TRIGGERED),   # formed after award: flagged as discrepancy in rationale, not scored
    (None, RuleStatus.INSUFFICIENT_DATA),
])
def test_newly_formed(formed, expected):
    bd = run([(entity("A", "Alpha Care", formed=formed), enr())])
    assert status(bd, RULE_NEWLY_FORMED) is expected


def test_newly_formed_ignores_minor_awards():
    bd = run([(entity("A", "Alpha Care", formed=date(2025, 2, 1), amount=50_000), enr())])
    assert status(bd, RULE_NEWLY_FORMED) is RuleStatus.NOT_TRIGGERED


# R2 --------------------------------------------------------------------------

@pytest.mark.parametrize("cls,amount,expected", [
    (LandUseClass.RESIDENTIAL, 250_001, RuleStatus.TRIGGERED),
    (LandUseClass.CMRA_MAIL_DROP, 400_000, RuleStatus.TRIGGERED),
    (LandUseClass.RESIDENTIAL, 250_000, RuleStatus.NOT_TRIGGERED),  # must EXCEED $250k
    (LandUseClass.COMMERCIAL, 900_000, RuleStatus.NOT_TRIGGERED),
    (LandUseClass.MIXED, 900_000, RuleStatus.NOT_TRIGGERED),
    (LandUseClass.UNKNOWN, 900_000, RuleStatus.INSUFFICIENT_DATA),
])
def test_residential(cls, amount, expected):
    bd = run([(entity("A", "Alpha Care", amount=amount), enr(cls))])
    assert status(bd, RULE_RESIDENTIAL) is expected


def test_po_box_triggers_even_without_geocode():
    e = entity("A", "Alpha Care", line1="PO Box 12", amount=500_000)
    bd = run([(e, EnrichmentData())])
    assert status(bd, RULE_RESIDENTIAL) is RuleStatus.TRIGGERED


# R3 --------------------------------------------------------------------------

def test_phantom_health():
    none_found = NPIMatchResult(performed=True, query_names=["ALPHA CARE*"])
    active = NPIMatchResult(performed=True, active_npi_found=True,
                            matches=[NPIRecord(npi="1", organization_name="ALPHA CARE LLC", status="A", name_similarity=100)])
    active.best_match = active.matches[0]
    failed = NPIMatchResult(performed=True, error="timeout")
    assert status(run([(entity("A", "Alpha Care", healthcare=True), enr(npi=none_found))]), RULE_PHANTOM_HEALTH) is RuleStatus.TRIGGERED
    assert status(run([(entity("A", "Alpha Care", healthcare=True), enr(npi=active))]), RULE_PHANTOM_HEALTH) is RuleStatus.NOT_TRIGGERED
    assert status(run([(entity("A", "Alpha Care", healthcare=True), enr(npi=failed))]), RULE_PHANTOM_HEALTH) is RuleStatus.INSUFFICIENT_DATA
    assert status(run([(entity("A", "Alpha Care"), enr(npi=none_found))]), RULE_PHANTOM_HEALTH) is RuleStatus.NOT_APPLICABLE


# R4 --------------------------------------------------------------------------

def test_colocation_requires_three_unrelated():
    a = entity("A", "Alpha Care", "9 Example Way", "Ste 5")
    b = entity("B", "Bravo Wellness", "9 Example Way", "#5")
    c = entity("C", "Charlie Partners", "9 EXAMPLE WAY UNIT 5")
    assert status(run([(a, enr()), (b, enr()), (c, enr())], "A"), RULE_COLOCATION) is RuleStatus.TRIGGERED
    # Different suite -> not co-located
    c2 = entity("C", "Charlie Partners", "9 Example Way", "Ste 6")
    assert status(run([(a, enr()), (b, enr()), (c2, enr())], "A"), RULE_COLOCATION) is RuleStatus.NOT_TRIGGERED


def test_colocation_discounts_affiliates():
    a = entity("A", "Alpha Care", "9 Example Way", uei="U1")
    a2 = entity("A2", "Alpha Care North", "9 Example Way")  # similar name -> affiliated
    b = entity("B", "Bravo Wellness", "9 Example Way")
    bd = run([(a, enr()), (a2, enr()), (b, enr())], "A")
    assert status(bd, RULE_COLOCATION) is RuleStatus.NOT_TRIGGERED


# R5 --------------------------------------------------------------------------

def test_exec_duplication_same_locality_only():
    a = entity("A", "Alpha Care", officers=["Jane Q. Sample"], line1="1 A St")
    b = entity("B", "Bravo Wellness", officers=["SAMPLE, JANE"], line1="2 B St")
    assert status(run([(a, enr()), (b, enr())], "A"), RULE_EXEC_DUP) is RuleStatus.TRIGGERED
    far = entity("B", "Bravo Wellness", officers=["SAMPLE, JANE"], line1="2 B St", city="Elsewhere")
    assert status(run([(a, enr()), (far, enr())], "A"), RULE_EXEC_DUP) is RuleStatus.NOT_TRIGGERED
    affiliated = entity("B", "Alpha Care West", officers=["Jane Sample"], line1="2 B St")
    assert status(run([(a, enr()), (affiliated, enr())], "A"), RULE_EXEC_DUP) is RuleStatus.NOT_TRIGGERED
    assert status(run([(entity("A", "Alpha Care"), enr())]), RULE_EXEC_DUP) is RuleStatus.INSUFFICIENT_DATA


# Totals ----------------------------------------------------------------------

def test_score_is_capped_sum_with_formula():
    a = entity("A", "Alpha Care", "9 Example Way", amount=600_000, formed=date(2025, 2, 1),
               officers=["Jane Sample"], healthcare=True)
    b = entity("B", "Bravo Wellness", "9 Example Way", officers=["Jane Sample"])
    c = entity("C", "Charlie Partners", "9 Example Way")
    pairs = [(a, enr(LandUseClass.RESIDENTIAL, NPIMatchResult(performed=True))), (b, enr()), (c, enr())]
    bd = run(pairs, "A")
    # registry profile: newly formed 20 + residential 25 + no NPI 30 + co-location 20 + serial 15 = 110
    assert bd.raw_total == 110 and bd.score == 100 and bd.max_possible == 200
    assert len(bd.flags) == 5
    assert "= min(100, 110) = 100" in bd.formula


def test_weights_are_configurable_and_deterministic():
    cfg = ScoringConfig(weights={RULE_NEWLY_FORMED: 50, RULE_RESIDENTIAL: 1, RULE_PHANTOM_HEALTH: 1, RULE_COLOCATION: 1, RULE_EXEC_DUP: 1})
    pairs = [(entity("A", "Alpha Care", formed=date(2025, 2, 1)), enr())]
    first = run(pairs, config=cfg)
    assert first.score == 50
    assert run(pairs, config=cfg).model_dump(exclude={"evaluations"}) == first.model_dump(exclude={"evaluations"})
