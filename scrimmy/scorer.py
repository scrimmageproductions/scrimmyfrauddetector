"""Stage: Deterministic Anomaly Engine.

The risk score is computed *only* here, by plain Python arithmetic over explicit
rules. No language model produces or adjusts a number. (The vision rule consumes
a model's image *description*, but the decision whether it counts is a fixed
threshold applied here.)

    score = min(100, sum(weight_i for each rule i whose status is TRIGGERED))

Every rule returns a ``RuleEvaluation`` regardless of outcome:

* TRIGGERED          - condition met; the profile weight is added (it may be 0 in a profile).
* NOT_TRIGGERED      - evaluated with adequate data; condition not met.
* INSUFFICIENT_DATA  - a required input was unavailable; contributes 0 points.
* NOT_APPLICABLE     - the rule does not apply to this entity.

Two weight profiles ship by default:

* ``registry``: public-registry signals plus capacity arithmetic (works with zero API keys).
* ``capacity``: weights licensed-capacity arithmetic and street-level vision checks.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import timedelta

from pydantic import BaseModel, Field

from .models import (
    AnomalyFlag,
    AwardeeEntity,
    EnrichmentData,
    EvidenceItem,
    LandUseClass,
    LEIEMatchStrength,
    RuleEvaluation,
    RuleStatus,
    ScoreBreakdown,
    SourceRecord,
    VisionCategory,
)
from .network_graph import EntityGraph

SCORER_VERSION = "2.0.0"

LEIE_EXCLUSION_MATCH = "LEIE_EXCLUSION_MATCH"
PHANTOM_HEALTHCARE_PROVIDER = "PHANTOM_HEALTHCARE_PROVIDER"
HIGH_DOLLAR_RESIDENTIAL_ZONE = "HIGH_DOLLAR_RESIDENTIAL_ZONE"
NEWLY_FORMED_WINDFALL = "NEWLY_FORMED_WINDFALL"
CO_LOCATED_SHELL_CLUSTER = "CO_LOCATED_SHELL_CLUSTER"
EXECUTIVE_SERIAL_REGISTRATION = "EXECUTIVE_SERIAL_REGISTRATION"
UNRESOLVED_PHYSICAL_DISCREPANCY = "UNRESOLVED_PHYSICAL_DISCREPANCY"
MATHEMATICAL_IMPOSSIBILITY = "MATHEMATICAL_IMPOSSIBILITY"
PHYSICAL_SITE_MISMATCH = "PHYSICAL_SITE_MISMATCH"

ALL_RULES = [
    LEIE_EXCLUSION_MATCH, MATHEMATICAL_IMPOSSIBILITY, PHANTOM_HEALTHCARE_PROVIDER, HIGH_DOLLAR_RESIDENTIAL_ZONE,
    PHYSICAL_SITE_MISMATCH, NEWLY_FORMED_WINDFALL, CO_LOCATED_SHELL_CLUSTER, EXECUTIVE_SERIAL_REGISTRATION,
    UNRESOLVED_PHYSICAL_DISCREPANCY,
]

# Neutral, evidentiary titles shown to humans. Rule IDs stay stable for machine use.
TITLES = {
    LEIE_EXCLUSION_MATCH: "Match to HHS-OIG List of Excluded Individuals/Entities",
    PHANTOM_HEALTHCARE_PROVIDER: "Healthcare Recipient Without Active Organizational NPI",
    HIGH_DOLLAR_RESIDENTIAL_ZONE: "High-Dollar Award at Residential / Mail-Drop Address",
    NEWLY_FORMED_WINDFALL: "Newly Formed Windfall",
    CO_LOCATED_SHELL_CLUSTER: "Unresolved Corporate Co-location",
    EXECUTIVE_SERIAL_REGISTRATION: "Serial Officer / Agent Registration",
    UNRESOLVED_PHYSICAL_DISCREPANCY: "Unresolved Physical Discrepancy",
    MATHEMATICAL_IMPOSSIBILITY: "Statistically Anomalous Disbursement-to-Capacity Ratio",
    PHYSICAL_SITE_MISMATCH: "Unverified Operating Capacity at Observed Site",
}

PROFILES: dict[str, dict] = {
    "registry": {
        "weights": {LEIE_EXCLUSION_MATCH: 40, PHANTOM_HEALTHCARE_PROVIDER: 30, HIGH_DOLLAR_RESIDENTIAL_ZONE: 25,
                    NEWLY_FORMED_WINDFALL: 20, CO_LOCATED_SHELL_CLUSTER: 20, EXECUTIVE_SERIAL_REGISTRATION: 15,
                    UNRESOLVED_PHYSICAL_DISCREPANCY: 10, MATHEMATICAL_IMPOSSIBILITY: 40, PHYSICAL_SITE_MISMATCH: 0},
        "serial_min_entities": 2,
    },
    "capacity": {
        "weights": {MATHEMATICAL_IMPOSSIBILITY: 40, LEIE_EXCLUSION_MATCH: 35, PHYSICAL_SITE_MISMATCH: 25,
                    CO_LOCATED_SHELL_CLUSTER: 20, PHANTOM_HEALTHCARE_PROVIDER: 20, EXECUTIVE_SERIAL_REGISTRATION: 15,
                    NEWLY_FORMED_WINDFALL: 15, HIGH_DOLLAR_RESIDENTIAL_ZONE: 0, UNRESOLVED_PHYSICAL_DISCREPANCY: 0},
        "serial_min_entities": 3,
    },
}

# NAICS codes whose services are delivered at the recipient's own premises (vs. in clients' homes).
ONSITE_NAICS_PREFIXES = ("624410", "623110", "621498", "621999", "622", "623")
ONSITE_VERTICALS = {"childcare", "hospice", "clinics"}
HOME_BASED_LICENSE_RE = ("FAMILY", "HOME", "GROUP DAY CARE HOME", "LICENSED HOME", "IN-HOME")


class ScoringConfig(BaseModel):
    """All tunables in one place, serialised into every scan manifest for reproducibility."""

    profile: str = "registry"
    weights: dict[str, int] = Field(default_factory=lambda: dict(PROFILES["registry"]["weights"]))
    newly_formed_max_days: int = Field(120, description="Formation-to-award gap strictly below this triggers.")
    major_award_min: float = Field(100_000.0, description="Minimum single-award amount considered a 'major disbursement'.")
    residential_award_min: float = Field(250_000.0, description="Total awarded must exceed this for the residential/site rules.")
    residential_classes: list[LandUseClass] = Field(
        default_factory=lambda: [LandUseClass.RESIDENTIAL, LandUseClass.CMRA_MAIL_DROP, LandUseClass.PO_BOX]
    )
    colocation_min_unrelated: int = 3
    serial_min_entities: int = Field(2, description="Unrelated entities (including this one) sharing one person.")
    affiliation_name_similarity: float = 88.0
    vision_min_confidence: float = 0.75
    vision_mismatch_categories: list[VisionCategory] = Field(default_factory=lambda: [
        VisionCategory.SINGLE_FAMILY_RESIDENCE, VisionCategory.COMMERCIAL_MAIL_RECEIVER_OR_PO_BOX, VisionCategory.VACANT_LOT,
    ])
    imagery_max_age_years: int = Field(4, description="Imagery captured more than this many years from the award date is not used.")
    high_priority_threshold: int = 50

    @classmethod
    def for_profile(cls, profile: str, **overrides) -> ScoringConfig:
        if profile not in PROFILES:
            raise ValueError(f"unknown scoring profile {profile!r}; choose from {', '.join(PROFILES)}")
        p = PROFILES[profile]
        return cls(profile=profile, weights=dict(p["weights"]), serial_min_entities=p["serial_min_entities"], **overrides)


@dataclass
class ScoringContext:
    """Cross-entity state for the network rules, plus scan-level facts the rules need."""

    graph: EntityGraph
    enrichments: dict[str, EnrichmentData]
    vertical: str | None = None
    entities: dict[str, AwardeeEntity] = field(default_factory=dict)

    @classmethod
    def build(cls, pairs: list[tuple[AwardeeEntity, EnrichmentData]], affiliation_threshold: float = 88.0,
              vertical: str | None = None) -> ScoringContext:
        graph = EntityGraph.build([e for e, _ in pairs], affiliation_threshold)
        return cls(graph=graph, enrichments={e.entity_key: d for e, d in pairs}, vertical=vertical, entities=graph.entities)

    def affiliation_group(self, entity_key: str) -> str:
        return self.graph.affiliation_group(entity_key)


RuleFn = Callable[[AwardeeEntity, EnrichmentData, ScoringContext], RuleEvaluation]


class DeterministicScorer:
    """Transparent, rule-based scorer. Same inputs always produce the same score."""

    TITLES = TITLES

    def __init__(self, config: ScoringConfig | None = None, source_lookup: Callable[[str], SourceRecord | None] | None = None):
        self.config = config or ScoringConfig()
        self._lookup = source_lookup or (lambda _id: None)
        self.rules: list[tuple[str, RuleFn]] = [
            (LEIE_EXCLUSION_MATCH, self._rule_leie),
            (MATHEMATICAL_IMPOSSIBILITY, self._rule_capacity),
            (PHANTOM_HEALTHCARE_PROVIDER, self._rule_phantom_health),
            (HIGH_DOLLAR_RESIDENTIAL_ZONE, self._rule_residential),
            (PHYSICAL_SITE_MISMATCH, self._rule_site_mismatch),
            (NEWLY_FORMED_WINDFALL, self._rule_newly_formed),
            (CO_LOCATED_SHELL_CLUSTER, self._rule_colocation),
            (EXECUTIVE_SERIAL_REGISTRATION, self._rule_serial),
            (UNRESOLVED_PHYSICAL_DISCREPANCY, self._rule_unresolved_physical),
        ]

    # -- public API -------------------------------------------------------- #

    def score(self, entity: AwardeeEntity, enrichment: EnrichmentData, context: ScoringContext) -> ScoreBreakdown:
        evaluations = [fn(entity, enrichment, context) for _, fn in self.rules]
        raw_total = sum(ev.points_awarded for ev in evaluations)
        max_possible = sum(self.config.weights.get(rid, 0) for rid, _ in self.rules)
        flags = [
            AnomalyFlag(rule_id=ev.rule_id, title=ev.title, description=ev.rationale, score_contribution=ev.points_awarded,
                        evidence=ev.evidence, related_entity_keys=ev.related_entity_keys)
            for ev in evaluations
            if ev.status is RuleStatus.TRIGGERED and ev.points_awarded > 0
        ]
        terms = " + ".join(f"{ev.weight}*{int(ev.status is RuleStatus.TRIGGERED)}" for ev in evaluations)
        score = min(100, raw_total)
        return ScoreBreakdown(
            entity_key=entity.entity_key, evaluations=evaluations, flags=flags, raw_total=raw_total,
            max_possible=max_possible, score=score,
            formula=f"score = min(100, {terms}) = min(100, {raw_total}) = {score}  [profile: {self.config.profile}]",
            scorer_version=SCORER_VERSION,
        )

    # -- helpers ----------------------------------------------------------- #

    def _ev(self, rule_id: str, status: RuleStatus, rationale: str, evidence: list[EvidenceItem] | None = None,
            related: list[str] | None = None) -> RuleEvaluation:
        weight = self.config.weights.get(rule_id, 0)
        if status is RuleStatus.TRIGGERED and weight == 0:
            rationale += f" [Observed but unscored: weight 0 in profile '{self.config.profile}'.]"
        return RuleEvaluation(
            rule_id=rule_id, title=TITLES[rule_id], weight=weight, status=status,
            points_awarded=weight if status is RuleStatus.TRIGGERED else 0,
            rationale=rationale, evidence=evidence or [], related_entity_keys=related or [],
        )

    def _evidence(self, record_ids: list[str], field_name: str, value: object, note: str | None = None) -> list[EvidenceItem]:
        items = []
        for rid in record_ids or [None]:  # type: ignore[list-item]
            rec = self._lookup(rid) if rid else None
            items.append(EvidenceItem(source_record_id=rid, source_url=rec.url if rec else None,
                                      retrieved_at=rec.retrieved_at if rec else None, field=field_name,
                                      observed_value=value, note=note))
        return items

    @staticmethod
    def _money(x: float) -> str:
        return f"${x:,.2f}"

    @staticmethod
    def _home_based_license(d: EnrichmentData) -> str | None:
        lic = d.license.best_match
        if lic and lic.license_type and any(k in lic.license_type.upper() for k in HOME_BASED_LICENSE_RE):
            return lic.license_type
        return None

    def _is_onsite(self, e: AwardeeEntity, ctx: ScoringContext) -> bool:
        if any(a.naics_code and a.naics_code.startswith("621610") for a in e.awards):
            return False  # home health: services delivered in clients' homes
        if any(a.naics_code and a.naics_code.startswith(ONSITE_NAICS_PREFIXES) for a in e.awards):
            return True
        return ctx.vertical in ONSITE_VERTICALS

    # -- LEIE -------------------------------------------------------------- #

    def _rule_leie(self, e: AwardeeEntity, d: EnrichmentData, ctx: ScoringContext) -> RuleEvaluation:
        r = d.leie
        if not r.performed:
            return self._ev(LEIE_EXCLUSION_MATCH, RuleStatus.INSUFFICIENT_DATA,
                            r.error or "No LEIE dataset loaded (supply --leie-csv or run `scrimmy leie-download`).")
        scored = [m for m in r.matches if m.strength in (LEIEMatchStrength.CONFIRMED_IDENTIFIER, LEIEMatchStrength.STRONG)]
        possible = [m for m in r.matches if m.strength is LEIEMatchStrength.POSSIBLE]
        ev = []
        for m in scored + possible:
            ev += self._evidence(r.source_record_ids[:1], f"leie.row[{m.leie_row_number}]",
                                 {"subject": m.subject, "role": m.subject_role, "strength": m.strength.value,
                                  "matched_on": m.matched_on, "exclusion_type": m.exclusion_type,
                                  "exclusion_date": m.exclusion_date.isoformat() if m.exclusion_date else None},
                                 note=f"LEIE file sha256 {r.dataset_sha256}")
        if scored:
            desc = "; ".join(f"'{m.subject}' ({m.subject_role}) matched LEIE row {m.leie_row_number} on "
                             f"{', '.join(m.matched_on)} [{m.strength.value}], exclusion type {m.exclusion_type or 'n/a'} "
                             f"effective {m.exclusion_date or 'n/a'}" for m in scored)
            return self._ev(LEIE_EXCLUSION_MATCH, RuleStatus.TRIGGERED,
                            f"{desc}. Identity of the individual/entity is not confirmed beyond the listed fields; "
                            f"verify against the OIG online search (which supports SSN/EIN verification).", ev)
        if possible:
            return self._ev(LEIE_EXCLUSION_MATCH, RuleStatus.NOT_TRIGGERED,
                            f"{len(possible)} name+state-only LEIE candidate(s) for human review; not scored "
                            f"because name agreement alone does not establish identity.", ev)
        return self._ev(LEIE_EXCLUSION_MATCH, RuleStatus.NOT_TRIGGERED,
                        f"No LEIE match for {len(r.subjects_screened)} subject(s) screened against {r.dataset_rows:,} active exclusions.")

    # -- Capacity arithmetic ---------------------------------------------- #

    def _rule_capacity(self, e: AwardeeEntity, d: EnrichmentData, ctx: ScoringContext) -> RuleEvaluation:
        c = d.capacity
        if not c.performed:
            return self._ev(MATHEMATICAL_IMPOSSIBILITY, RuleStatus.INSUFFICIENT_DATA, c.reason_not_performed or "Capacity analysis not performed.")
        lic = d.license.best_match
        ev = self._evidence(lic.source_record_ids if lic else [], "license.capacity",
                            {"facility": lic.facility_name if lic else None, "license_number": lic.license_number if lic else None,
                             "capacity": c.licensed_capacity, "license_type": lic.license_type if lic else None,
                             "name_similarity": lic.name_similarity if lic else None})
        ev += self._evidence([], "capacity.formula", c.formula, note=f"benchmark: {c.benchmark_label}; {c.benchmark_citation}")
        assert c.capacity_ratio is not None and c.ratio_threshold is not None
        basis = ("the state-licensed capacity" if c.capacity_basis == "state_license"
                 else "the facility-type plausibility ceiling (no state license matched)")
        if c.capacity_ratio > c.ratio_threshold:
            return self._ev(
                MATHEMATICAL_IMPOSSIBILITY, RuleStatus.TRIGGERED,
                f"Federal disbursements of {self._money(c.award_total or 0)} imply ~{c.estimated_daily_beneficiaries:,.1f} "
                f"beneficiaries per day at the benchmark rate, {c.capacity_ratio:.2f}x {basis} of {c.licensed_capacity} "
                f"(threshold > {c.ratio_threshold}x). Statistically anomalous disbursement-to-capacity ratio; assumes the "
                f"award funded beneficiary-days at a single site.", ev)
        return self._ev(MATHEMATICAL_IMPOSSIBILITY, RuleStatus.NOT_TRIGGERED,
                        f"Implied {c.estimated_daily_beneficiaries:,.1f} beneficiaries/day = {c.capacity_ratio:.2f}x {basis} of "
                        f"{c.licensed_capacity} (threshold > {c.ratio_threshold}x).", ev)

    # -- NPI --------------------------------------------------------------- #

    def _rule_phantom_health(self, e: AwardeeEntity, d: EnrichmentData, ctx: ScoringContext) -> RuleEvaluation:
        if not e.is_healthcare:
            return self._ev(PHANTOM_HEALTHCARE_PROVIDER, RuleStatus.NOT_APPLICABLE, "Recipient not classified as a healthcare provider.")
        npi = d.npi
        if not npi.performed:
            return self._ev(PHANTOM_HEALTHCARE_PROVIDER, RuleStatus.INSUFFICIENT_DATA, "NPPES lookup was not performed.")
        if npi.error:
            return self._ev(PHANTOM_HEALTHCARE_PROVIDER, RuleStatus.INSUFFICIENT_DATA, f"NPPES lookup failed: {npi.error}",
                            self._evidence(npi.source_record_ids, "nppes.error", npi.error))
        ev = self._evidence(npi.source_record_ids[:1] + npi.source_record_ids[-1:] if npi.source_record_ids else [], "nppes.results",
                            {"name_queries": npi.query_names, "state": e.address.state, "zip_searched": npi.postal_code_searched,
                             "zip_organizations": npi.postal_candidates, "candidates_examined": npi.candidates_examined,
                             "stages": npi.stages,
                             "accepted_matches": [(m.npi, m.organization_name, m.status, m.match_method, m.name_similarity) for m in npi.matches]},
                            note=f"healthcare basis: {e.healthcare_basis}")
        if npi.active_npi_found and npi.best_match:
            b = npi.best_match
            how = {"name_search": "by name", "postal_street_number": f"by street number in ZIP {npi.postal_code_searched}",
                   "postal_fuzzy_name": f"by fuzzy name in ZIP {npi.postal_code_searched}"}.get(b.match_method or "", "")
            return self._ev(PHANTOM_HEALTHCARE_PROVIDER, RuleStatus.NOT_TRIGGERED,
                            f"Active organizational NPI {b.npi} ({b.organization_name}) matched {how} "
                            f"(name similarity {b.name_similarity:.0f}).", ev)
        return self._ev(
            PHANTOM_HEALTHCARE_PROVIDER, RuleStatus.TRIGGERED,
            f"Healthcare-classified recipient ({e.healthcare_basis}) has no active Type-2 (organizational) NPI in NPPES: "
            f"{len(npi.query_names)} name variant(s) in {e.address.state or 'any state'} and a sweep of all "
            f"{npi.postal_candidates} organization(s) in ZIP {npi.postal_code_searched} (street-number and fuzzy-name "
            f"matching) found none. Unverified provider-registration discrepancy.", ev)

    # -- Residential / mail drop (OSM) ------------------------------------- #

    def _rule_residential(self, e: AwardeeEntity, d: EnrichmentData, ctx: ScoringContext) -> RuleEvaluation:
        cfg = self.config
        total = e.total_awarded
        amount_ev = [item for a in sorted(e.awards, key=lambda a: -a.amount)
                     for item in self._evidence(a.source_record_ids[:1], "award.amount", {"award_id": a.award_id, "amount": a.amount})]
        amount_ev += self._evidence([], "sum(award.amount)", total, note=f"derived: {len(e.awards)} award(s) in scope")
        if total <= cfg.residential_award_min:
            return self._ev(HIGH_DOLLAR_RESIDENTIAL_ZONE, RuleStatus.NOT_TRIGGERED,
                            f"Total awarded in scope {self._money(total)} does not exceed {self._money(cfg.residential_award_min)}.")
        lu = d.land_use
        if e.address.is_po_box or e.address.is_pmb:
            cls = LandUseClass.PO_BOX if e.address.is_po_box else LandUseClass.CMRA_MAIL_DROP
        else:
            cls = lu.classification
        if cls is LandUseClass.UNKNOWN or (not lu.performed and not (e.address.is_po_box or e.address.is_pmb)):
            reason = lu.error or "; ".join(lu.signals) or "address could not be geocoded/classified"
            return self._ev(HIGH_DOLLAR_RESIDENTIAL_ZONE, RuleStatus.INSUFFICIENT_DATA,
                            f"Total awarded {self._money(total)} exceeds threshold, but the premises could not be classified ({reason}).", amount_ev)
        loc_ev = self._evidence(d.geocode.source_record_ids, "census.addressMatches[0]",
                                {"input": e.address.one_line, "matched": d.geocode.matched_address,
                                 "lat": d.geocode.latitude, "lon": d.geocode.longitude}) if d.geocode.performed else []
        lu_ev = self._evidence(lu.source_record_ids, "osm.classification",
                               {"classification": cls.value, "signals": lu.signals, "nearest_building": lu.nearest_building_tag,
                                "landuse": lu.landuse_tags})
        evidence = amount_ev + loc_ev + lu_ev
        home_license = self._home_based_license(d)
        if cls is LandUseClass.RESIDENTIAL and home_license:
            return self._ev(HIGH_DOLLAR_RESIDENTIAL_ZONE, RuleStatus.NOT_TRIGGERED,
                            f"Residential premises, but the matched state license type '{home_license}' is home-based, which is "
                            f"consistent with operating from a residence.", evidence)
        if cls in cfg.residential_classes:
            label = {LandUseClass.RESIDENTIAL: "a mapped residential structure",
                     LandUseClass.CMRA_MAIL_DROP: "a commercial mail-receiving agency (CMRA) / private mailbox pattern",
                     LandUseClass.PO_BOX: "a USPS Post Office Box"}[cls]
            return self._ev(HIGH_DOLLAR_RESIDENTIAL_ZONE, RuleStatus.TRIGGERED,
                            f"{self._money(total)} awarded to an address of record that resolves to {label}. Statistically "
                            f"anomalous disbursement-to-presence ratio; unverified physical discrepancy.", evidence)
        return self._ev(HIGH_DOLLAR_RESIDENTIAL_ZONE, RuleStatus.NOT_TRIGGERED,
                        f"Premises classified as '{cls.value}', which is not in the residential/mail-drop set.", evidence)

    # -- Vision site check ------------------------------------------------- #

    def _rule_site_mismatch(self, e: AwardeeEntity, d: EnrichmentData, ctx: ScoringContext) -> RuleEvaluation:
        cfg, v = self.config, d.vision
        if not self._is_onsite(e, ctx):
            return self._ev(PHYSICAL_SITE_MISMATCH, RuleStatus.NOT_APPLICABLE,
                            "Funded services are not delivered at the recipient's premises (e.g. home health), so site appearance is not probative.")
        if e.total_awarded <= cfg.residential_award_min:
            return self._ev(PHYSICAL_SITE_MISMATCH, RuleStatus.NOT_TRIGGERED,
                            f"Total awarded {self._money(e.total_awarded)} does not exceed {self._money(cfg.residential_award_min)}.")
        if not v.performed or not v.assessment or not v.imagery:
            return self._ev(PHYSICAL_SITE_MISMATCH, RuleStatus.INSUFFICIENT_DATA, v.error or "Street-level vision check not run.")
        img, a = v.imagery, v.assessment
        ev = self._evidence(img.source_record_ids, "vision.assessment",
                            {"category": a.category.value, "confidence": a.confidence, "markers": a.physical_markers,
                             "imagery": {"provider": img.provider, "id": img.image_id, "captured_at": str(img.captured_at),
                                         "distance_m": img.distance_m, "heading": img.heading_deg}, "model": v.model},
                            note="Category/confidence are the vision model's description; the threshold decision is deterministic.")
        award_dates = [x.effective_date for x in e.awards if x.effective_date]
        if img.captured_at and award_dates:
            window = timedelta(days=365 * cfg.imagery_max_age_years)
            if not any(abs(img.captured_at - ad) <= window for ad in award_dates):
                return self._ev(PHYSICAL_SITE_MISMATCH, RuleStatus.INSUFFICIENT_DATA,
                                f"Imagery captured {img.captured_at} is more than {cfg.imagery_max_age_years} years from every award date.", ev)
        if a.category not in cfg.vision_mismatch_categories:
            return self._ev(PHYSICAL_SITE_MISMATCH, RuleStatus.NOT_TRIGGERED, f"Imagery classified as {a.category.value} ({a.confidence:.2f}).", ev)
        if a.confidence < cfg.vision_min_confidence:
            return self._ev(PHYSICAL_SITE_MISMATCH, RuleStatus.INSUFFICIENT_DATA,
                            f"Imagery classified as {a.category.value} with confidence {a.confidence:.2f} (< {cfg.vision_min_confidence}).", ev)
        if d.land_use.classification in (LandUseClass.COMMERCIAL, LandUseClass.MIXED):
            return self._ev(PHYSICAL_SITE_MISMATCH, RuleStatus.NOT_TRIGGERED,
                            f"Imagery classified as {a.category.value}, but OSM maps commercial presence at the address "
                            f"({d.land_use.classification.value}); conflicting evidence is not scored.", ev)
        home_license = self._home_based_license(d)
        if a.category is VisionCategory.SINGLE_FAMILY_RESIDENCE and home_license:
            return self._ev(PHYSICAL_SITE_MISMATCH, RuleStatus.NOT_TRIGGERED,
                            f"Residence observed, consistent with home-based license type '{home_license}'.", ev)
        return self._ev(
            PHYSICAL_SITE_MISMATCH, RuleStatus.TRIGGERED,
            f"Recipient of {self._money(e.total_awarded)} for on-site services; street-level imagery ({img.provider}, captured "
            f"{img.captured_at or 'date unknown'}) was classified {a.category.value} (confidence {a.confidence:.2f}) with no "
            f"commercial presence mapped. Unverified operating capacity at observed site.", ev)

    # -- Formation --------------------------------------------------------- #

    def _rule_newly_formed(self, e: AwardeeEntity, d: EnrichmentData, ctx: ScoringContext) -> RuleEvaluation:
        cfg = self.config
        if not e.formation_date:
            return self._ev(NEWLY_FORMED_WINDFALL, RuleStatus.INSUFFICIENT_DATA,
                            "No business formation/registration date available from an authoritative source.")
        major = [a for a in e.awards if a.amount >= cfg.major_award_min and a.effective_date]
        if not major:
            return self._ev(NEWLY_FORMED_WINDFALL, RuleStatus.NOT_TRIGGERED, f"No dated award of at least {self._money(cfg.major_award_min)} in scope.")
        gaps = sorted(((a.effective_date - e.formation_date).days, i, a) for i, a in enumerate(major))  # type: ignore[operator]
        non_negative = [(g, a) for g, _, a in gaps if g >= 0]
        formation_ev = self._evidence(e.formation_source_record_ids, "formation_date", e.formation_date.isoformat(),
                                      note=f"source: {e.formation_date_source}")
        if not non_negative:
            return self._ev(NEWLY_FORMED_WINDFALL, RuleStatus.NOT_TRIGGERED,
                            f"All major awards pre-date the recorded formation date ({e.formation_date}); the date source should be "
                            f"verified (unverified record discrepancy).", formation_ev)
        gap, award = non_negative[0]
        award_ev = self._evidence(award.source_record_ids, "award.effective_date / amount",
                                  {"award_id": award.award_id, "date": award.effective_date.isoformat(), "amount": award.amount})  # type: ignore[union-attr]
        if gap < cfg.newly_formed_max_days:
            return self._ev(NEWLY_FORMED_WINDFALL, RuleStatus.TRIGGERED,
                            f"Award {award.award_id} ({self._money(award.amount)}) has an effective date {gap} days after the recorded "
                            f"formation date {e.formation_date} (threshold: < {cfg.newly_formed_max_days} days). High-variance "
                            f"award-to-operating-history ratio.", formation_ev + award_ev)
        return self._ev(NEWLY_FORMED_WINDFALL, RuleStatus.NOT_TRIGGERED,
                        f"Shortest formation-to-major-award gap is {gap} days (threshold: < {cfg.newly_formed_max_days}).", formation_ev + award_ev)

    # -- Network ----------------------------------------------------------- #

    def _rule_colocation(self, e: AwardeeEntity, d: EnrichmentData, ctx: ScoringContext) -> RuleEvaluation:
        key = e.address.location_key
        if not key:
            return self._ev(CO_LOCATED_SHELL_CLUSTER, RuleStatus.INSUFFICIENT_DATA, "Address of record lacks a street/ZIP for exact matching.")
        members, unrelated = ctx.graph.colocated(e.entity_key)
        others = [k for k in members if k != e.entity_key]
        ev = self._evidence(sorted({r for k in members for r in ctx.entities[k].source_record_ids[-1:]}), "address.location_key",
                            {"location_key": key, "entities": [ctx.entities[k].name for k in members], "unrelated_groups": unrelated},
                            note="Co-location is measured only among recipients returned by this scan.")
        if unrelated >= self.config.colocation_min_unrelated:
            return self._ev(CO_LOCATED_SHELL_CLUSTER, RuleStatus.TRIGGERED,
                            f"{len(members)} awardees ({unrelated} with no shared UEI/parent or similar name) list the identical address "
                            f"of record '{key.replace('|', ' ')}'. Unresolved corporate co-location.", ev, others)
        return self._ev(CO_LOCATED_SHELL_CLUSTER, RuleStatus.NOT_TRIGGERED,
                        f"{unrelated} unrelated awardee group(s) at this exact address (threshold: {self.config.colocation_min_unrelated}).",
                        ev if others else [], others)

    def _rule_serial(self, e: AwardeeEntity, d: EnrichmentData, ctx: ScoringContext) -> RuleEvaluation:
        if not e.officers:
            return self._ev(EXECUTIVE_SERIAL_REGISTRATION, RuleStatus.INSUFFICIENT_DATA,
                            "No officers, authorized officials or registered agents published for this recipient.")
        shared = ctx.graph.shared_people(e.entity_key, same_locality=True)
        need = self.config.serial_min_entities
        hits = {p: ks for p, ks in shared.items() if 1 + len(ks) >= need}
        if not hits:
            return self._ev(EXECUTIVE_SERIAL_REGISTRATION, RuleStatus.NOT_TRIGGERED,
                            f"No listed person appears on {need}+ unrelated awardees in the same locality "
                            f"({len(e.officers)} person record(s) checked).")
        evidence: list[EvidenceItem] = []
        for person, keys in hits.items():
            evidence += self._evidence(ctx.graph.edge_sources(e.entity_key, person)[:1], f"{e.name}.officers",
                                       ctx.graph.listed_as(e.entity_key, person))
            for k in keys:
                evidence += self._evidence(ctx.graph.edge_sources(k, person)[:1], f"{ctx.entities[k].name}.officers",
                                           ctx.graph.listed_as(k, person), note="Name-only match; identity of the individual is not confirmed.")
        desc = "; ".join(f"'{ctx.graph.listed_as(e.entity_key, p)}' is listed on {1 + len(ks)} unrelated awardees" for p, ks in hits.items())
        related = sorted({k for ks in hits.values() for k in ks})
        return self._ev(EXECUTIVE_SERIAL_REGISTRATION, RuleStatus.TRIGGERED,
                        f"{desc} in {(e.address.locality_key or 'unknown').replace('|', ', ')} (threshold: {need}). "
                        f"Anomalous officer-overlap pattern.", evidence, related)

    # -- Geocoding --------------------------------------------------------- #

    def _rule_unresolved_physical(self, e: AwardeeEntity, d: EnrichmentData, ctx: ScoringContext) -> RuleEvaluation:
        g = d.geocode
        if e.address.is_po_box:
            return self._ev(UNRESOLVED_PHYSICAL_DISCREPANCY, RuleStatus.NOT_APPLICABLE, "P.O. Box addresses are handled by the residential/mail-drop rule.")
        if not g.performed:
            return self._ev(UNRESOLVED_PHYSICAL_DISCREPANCY, RuleStatus.INSUFFICIENT_DATA, "Address incomplete; geocoding not attempted.")
        if g.error:
            return self._ev(UNRESOLVED_PHYSICAL_DISCREPANCY, RuleStatus.INSUFFICIENT_DATA, f"Census Geocoder request failed: {g.error}")
        ev = self._evidence(g.source_record_ids, "census.addressMatches", {"input": g.input_address, "matched": g.matched_address})
        if g.matched:
            return self._ev(UNRESOLVED_PHYSICAL_DISCREPANCY, RuleStatus.NOT_TRIGGERED, f"Census matched '{g.matched_address}'.", ev)
        return self._ev(UNRESOLVED_PHYSICAL_DISCREPANCY, RuleStatus.TRIGGERED,
                        f"Address of record '{g.input_address}' did not match any Census TIGER address range. Unresolved physical "
                        f"discrepancy (new construction, rural routes and typos are common lawful causes).", ev)
