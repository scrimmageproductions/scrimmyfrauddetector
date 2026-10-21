"""Stage 3 - Deterministic Anomaly Engine.

The risk score is computed *only* here, by plain Python arithmetic over
explicit, documented rules. No language model touches the numbers.

    score = min(100, sum(weight_i for each rule i whose status is TRIGGERED))

Each rule returns a ``RuleEvaluation`` regardless of outcome, so a reviewer can
see why a rule did *not* fire (including "insufficient data") as well as why
it did. Rule statuses:

* TRIGGERED          - condition met; weight is added.
* NOT_TRIGGERED      - evaluated with adequate data; condition not met.
* INSUFFICIENT_DATA  - a required input was unavailable; contributes 0 points.
* NOT_APPLICABLE     - the rule does not apply to this entity (e.g. NPI checks on non-healthcare).
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass, field

from pydantic import BaseModel, Field

from .models import (
    AnomalyFlag,
    AwardeeEntity,
    EnrichmentData,
    EvidenceItem,
    LandUseClass,
    RuleEvaluation,
    RuleStatus,
    ScoreBreakdown,
    SourceRecord,
)
from .normalize import names_related

SCORER_VERSION = "1.0.0"

RULE_NEWLY_FORMED = "R1_NEWLY_FORMED_WINDFALL"
RULE_RESIDENTIAL = "R2_HIGH_DOLLAR_RESIDENTIAL_MISMATCH"
RULE_PHANTOM_HEALTH = "R3_PHANTOM_HEALTH_PROVIDER"
RULE_COLOCATION = "R4_MULTIPLE_SHELL_COLOCATION"
RULE_EXEC_DUP = "R5_EXECUTIVE_DUPLICATION"


class ScoringConfig(BaseModel):
    """All tunables in one place, serialised into every dossier for reproducibility."""

    weights: dict[str, int] = Field(
        default_factory=lambda: {
            RULE_NEWLY_FORMED: 30,
            RULE_RESIDENTIAL: 25,
            RULE_PHANTOM_HEALTH: 25,
            RULE_COLOCATION: 20,
            RULE_EXEC_DUP: 15,
        }
    )
    newly_formed_max_days: int = Field(120, description="Formation-to-award gap strictly below this triggers R1.")
    major_award_min: float = Field(100_000.0, description="Minimum single-award amount considered 'major' for R1.")
    residential_award_min: float = Field(250_000.0, description="Total awarded must exceed this for R2.")
    residential_classes: list[LandUseClass] = Field(
        default_factory=lambda: [LandUseClass.RESIDENTIAL, LandUseClass.CMRA_MAIL_DROP, LandUseClass.PO_BOX]
    )
    colocation_min_unrelated: int = Field(3, description="Unrelated awardees at one exact address needed for R4.")
    exec_min_disconnected_entities: int = Field(2, description="Disconnected entities sharing one officer needed for R5.")
    affiliation_name_similarity: float = Field(88.0, description="Names at/above this similarity are treated as affiliated.")


# --------------------------------------------------------------------------- #
# Cross-entity context
# --------------------------------------------------------------------------- #


class _UnionFind:
    def __init__(self) -> None:
        self.parent: dict[str, str] = {}

    def find(self, x: str) -> str:
        self.parent.setdefault(x, x)
        while self.parent[x] != x:
            self.parent[x] = self.parent[self.parent[x]]
            x = self.parent[x]
        return x

    def union(self, a: str, b: str) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.parent[max(ra, rb)] = min(ra, rb)


@dataclass
class ScoringContext:
    """Indexes over *all* entities in the scan, used by the cross-entity rules (R4, R5)."""

    entities: dict[str, AwardeeEntity]
    enrichments: dict[str, EnrichmentData]
    affiliation_threshold: float = 88.0
    by_location: dict[str, list[str]] = field(default_factory=lambda: defaultdict(list))
    by_officer: dict[str, list[tuple[str, str]]] = field(default_factory=lambda: defaultdict(list))
    _affiliation: _UnionFind = field(default_factory=_UnionFind)

    @classmethod
    def build(cls, pairs: list[tuple[AwardeeEntity, EnrichmentData]], affiliation_threshold: float = 88.0) -> ScoringContext:
        ctx = cls(entities={e.entity_key: e for e, _ in pairs}, enrichments={e.entity_key: d for e, d in pairs},
                  affiliation_threshold=affiliation_threshold)
        for e, _ in pairs:
            if e.address.location_key:
                ctx.by_location[e.address.location_key].append(e.entity_key)
            for off in e.officers:
                if off.normalized_name and len(off.normalized_name.split()) >= 2:
                    ctx.by_officer[off.normalized_name].append((e.entity_key, off.name))
        ctx._build_affiliations()
        return ctx

    def _build_affiliations(self) -> None:
        """Entities are 'related' if they share a UEI/parent UEI or have near-identical names."""
        ents = list(self.entities.values())
        for e in ents:
            self._affiliation.find(e.entity_key)
        by_parent: dict[str, list[str]] = defaultdict(list)
        for e in ents:
            for anchor in {e.uei, e.parent_uei} - {None, ""}:
                by_parent[anchor].append(e.entity_key)  # type: ignore[index]
        for members in by_parent.values():
            for m in members[1:]:
                self._affiliation.union(members[0], m)
        for i, a in enumerate(ents):
            for b in ents[i + 1 :]:
                if names_related(a.normalized_name, b.normalized_name, self.affiliation_threshold):
                    self._affiliation.union(a.entity_key, b.entity_key)

    def affiliation_group(self, entity_key: str) -> str:
        return self._affiliation.find(entity_key)


# --------------------------------------------------------------------------- #
# Scorer
# --------------------------------------------------------------------------- #

RuleFn = Callable[[AwardeeEntity, EnrichmentData, ScoringContext], RuleEvaluation]


class DeterministicScorer:
    """Transparent, rule-based scorer. Same inputs always produce the same score."""

    TITLES = {
        RULE_NEWLY_FORMED: "Newly Formed Windfall",
        RULE_RESIDENTIAL: "High-Dollar Residential / Mail-Drop Address Mismatch",
        RULE_PHANTOM_HEALTH: "Healthcare Recipient Without Active Organizational NPI",
        RULE_COLOCATION: "Anomalous Multi-Entity Co-location",
        RULE_EXEC_DUP: "Officer Shared Across Disconnected Awardees",
    }

    def __init__(self, config: ScoringConfig | None = None, source_lookup: Callable[[str], SourceRecord | None] | None = None):
        self.config = config or ScoringConfig()
        self._lookup = source_lookup or (lambda _id: None)
        self.rules: list[tuple[str, RuleFn]] = [
            (RULE_NEWLY_FORMED, self._rule_newly_formed),
            (RULE_RESIDENTIAL, self._rule_residential),
            (RULE_PHANTOM_HEALTH, self._rule_phantom_health),
            (RULE_COLOCATION, self._rule_colocation),
            (RULE_EXEC_DUP, self._rule_exec_dup),
        ]

    # -- public API -------------------------------------------------------- #

    def score(self, entity: AwardeeEntity, enrichment: EnrichmentData, context: ScoringContext) -> ScoreBreakdown:
        evaluations = [fn(entity, enrichment, context) for _, fn in self.rules]
        raw_total = sum(ev.points_awarded for ev in evaluations)
        max_possible = sum(self.config.weights[rid] for rid, _ in self.rules)
        flags = [
            AnomalyFlag(
                rule_id=ev.rule_id,
                title=ev.title,
                description=ev.rationale,
                score_contribution=ev.points_awarded,
                evidence=ev.evidence,
                related_entity_keys=ev.related_entity_keys,
            )
            for ev in evaluations
            if ev.status is RuleStatus.TRIGGERED
        ]
        terms = " + ".join(f"{ev.weight}*{int(ev.status is RuleStatus.TRIGGERED)}" for ev in evaluations)
        score = min(100, raw_total)
        return ScoreBreakdown(
            entity_key=entity.entity_key,
            evaluations=evaluations,
            flags=flags,
            raw_total=raw_total,
            max_possible=max_possible,
            score=score,
            formula=f"score = min(100, {terms}) = min(100, {raw_total}) = {score}",
            scorer_version=SCORER_VERSION,
        )

    # -- helpers ----------------------------------------------------------- #

    def _ev(self, rule_id: str, status: RuleStatus, rationale: str, evidence: list[EvidenceItem] | None = None,
            related: list[str] | None = None) -> RuleEvaluation:
        weight = self.config.weights[rule_id]
        return RuleEvaluation(
            rule_id=rule_id,
            title=self.TITLES[rule_id],
            weight=weight,
            status=status,
            points_awarded=weight if status is RuleStatus.TRIGGERED else 0,
            rationale=rationale,
            evidence=evidence or [],
            related_entity_keys=related or [],
        )

    def _evidence(self, record_ids: list[str], field_name: str, value: object, note: str | None = None) -> list[EvidenceItem]:
        """One EvidenceItem per supporting SourceRecord (or a single derived item if none)."""
        items = []
        for rid in record_ids or [None]:  # type: ignore[list-item]
            rec = self._lookup(rid) if rid else None
            items.append(
                EvidenceItem(
                    source_record_id=rid,
                    source_url=rec.url if rec else None,
                    retrieved_at=rec.retrieved_at if rec else None,
                    field=field_name,
                    observed_value=value,
                    note=note,
                )
            )
        return items

    @staticmethod
    def _money(x: float) -> str:
        return f"${x:,.2f}"

    # -- R1 ---------------------------------------------------------------- #

    def _rule_newly_formed(self, e: AwardeeEntity, d: EnrichmentData, ctx: ScoringContext) -> RuleEvaluation:
        cfg = self.config
        if not e.formation_date:
            return self._ev(RULE_NEWLY_FORMED, RuleStatus.INSUFFICIENT_DATA,
                            "No business formation/registration date available from an authoritative source.")
        major = [a for a in e.awards if a.amount >= cfg.major_award_min and a.effective_date]
        if not major:
            return self._ev(RULE_NEWLY_FORMED, RuleStatus.NOT_TRIGGERED,
                            f"No dated award of at least {self._money(cfg.major_award_min)} in scope.")
        gaps = sorted(((a.effective_date - e.formation_date).days, a) for a in major)  # type: ignore[operator]
        non_negative = [(g, a) for g, a in gaps if g >= 0]
        formation_ev = self._evidence(e.formation_source_record_ids, "formation_date", e.formation_date.isoformat(),
                                      note=f"source: {e.formation_date_source}")
        if not non_negative:
            g, a = gaps[-1]
            return self._ev(RULE_NEWLY_FORMED, RuleStatus.NOT_TRIGGERED,
                            f"All major awards pre-date the recorded formation date ({e.formation_date}); "
                            f"the date source should be verified (unverified record discrepancy).",
                            formation_ev)
        gap, award = non_negative[0]
        award_ev = self._evidence(award.source_record_ids, "award.effective_date / amount",
                                  {"award_id": award.award_id, "date": award.effective_date.isoformat(), "amount": award.amount})  # type: ignore[union-attr]
        if gap < cfg.newly_formed_max_days:
            return self._ev(
                RULE_NEWLY_FORMED, RuleStatus.TRIGGERED,
                f"Award {award.award_id} ({self._money(award.amount)}) has an effective date {gap} days after the "
                f"recorded formation date {e.formation_date} (threshold: < {cfg.newly_formed_max_days} days). "
                f"High-variance award-to-operating-history ratio.",
                formation_ev + award_ev,
            )
        return self._ev(RULE_NEWLY_FORMED, RuleStatus.NOT_TRIGGERED,
                        f"Shortest formation-to-major-award gap is {gap} days (threshold: < {cfg.newly_formed_max_days}).",
                        formation_ev + award_ev)

    # -- R2 ---------------------------------------------------------------- #

    def _rule_residential(self, e: AwardeeEntity, d: EnrichmentData, ctx: ScoringContext) -> RuleEvaluation:
        cfg = self.config
        total = e.total_awarded
        amount_ev = [
            item
            for a in sorted(e.awards, key=lambda a: -a.amount)
            for item in self._evidence(a.source_record_ids[:1], "award.amount", {"award_id": a.award_id, "amount": a.amount})
        ] + self._evidence([], "sum(award.amount)", total, note=f"derived: {len(e.awards)} award(s) in scope")
        if total <= cfg.residential_award_min:
            return self._ev(RULE_RESIDENTIAL, RuleStatus.NOT_TRIGGERED,
                            f"Total awarded in scope {self._money(total)} does not exceed {self._money(cfg.residential_award_min)}.")
        lu = d.land_use
        if e.address.is_po_box or e.address.is_pmb:
            cls = LandUseClass.PO_BOX if e.address.is_po_box else LandUseClass.CMRA_MAIL_DROP
        else:
            cls = lu.classification
        if cls in (LandUseClass.UNKNOWN,) or (not lu.performed and not (e.address.is_po_box or e.address.is_pmb)):
            reason = lu.error or "; ".join(lu.signals) or "address could not be geocoded/classified"
            return self._ev(RULE_RESIDENTIAL, RuleStatus.INSUFFICIENT_DATA,
                            f"Total awarded {self._money(total)} exceeds threshold, but the premises could not be classified ({reason}).",
                            amount_ev)
        loc_ev = self._evidence(d.geocode.source_record_ids, "census.addressMatches[0]",
                                {"input": e.address.one_line, "matched": d.geocode.matched_address,
                                 "lat": d.geocode.latitude, "lon": d.geocode.longitude})
        lu_ev = self._evidence(lu.source_record_ids, "osm.classification",
                               {"classification": cls.value, "signals": lu.signals,
                                "nearest_building": lu.nearest_building_tag, "landuse": lu.landuse_tags})
        evidence = amount_ev + (loc_ev if d.geocode.performed else []) + lu_ev
        if cls in cfg.residential_classes:
            label = {
                LandUseClass.RESIDENTIAL: "a mapped residential structure",
                LandUseClass.CMRA_MAIL_DROP: "a commercial mail-receiving agency (CMRA) / private mailbox pattern",
                LandUseClass.PO_BOX: "a USPS Post Office Box",
            }[cls]
            return self._ev(
                RULE_RESIDENTIAL, RuleStatus.TRIGGERED,
                f"{self._money(total)} awarded to an address of record that resolves to {label}. "
                f"Unverified physical discrepancy between award scale and observable business premises.",
                evidence,
            )
        return self._ev(RULE_RESIDENTIAL, RuleStatus.NOT_TRIGGERED,
                        f"Premises classified as '{cls.value}', which is not in the residential/mail-drop set.", evidence)

    # -- R3 ---------------------------------------------------------------- #

    def _rule_phantom_health(self, e: AwardeeEntity, d: EnrichmentData, ctx: ScoringContext) -> RuleEvaluation:
        if not e.is_healthcare:
            return self._ev(RULE_PHANTOM_HEALTH, RuleStatus.NOT_APPLICABLE, "Recipient not classified as a healthcare provider.")
        npi = d.npi
        if not npi.performed:
            return self._ev(RULE_PHANTOM_HEALTH, RuleStatus.INSUFFICIENT_DATA, "NPPES lookup was not performed.")
        if npi.error:
            return self._ev(RULE_PHANTOM_HEALTH, RuleStatus.INSUFFICIENT_DATA, f"NPPES lookup failed: {npi.error}",
                            self._evidence(npi.source_record_ids, "nppes.error", npi.error))
        ev = self._evidence(npi.source_record_ids, "nppes.results",
                            {"queries": npi.query_names, "state": e.address.state, "candidates_examined": npi.candidates_examined,
                             "accepted_matches": [(m.npi, m.organization_name, m.status, m.name_similarity) for m in npi.matches]},
                            note=f"healthcare basis: {e.healthcare_basis}")
        if npi.active_npi_found:
            best = npi.best_match
            return self._ev(RULE_PHANTOM_HEALTH, RuleStatus.NOT_TRIGGERED,
                            f"Active organizational NPI {best.npi} ({best.organization_name}) matched "
                            f"(name similarity {best.name_similarity:.0f}).", ev)  # type: ignore[union-attr]
        return self._ev(
            RULE_PHANTOM_HEALTH, RuleStatus.TRIGGERED,
            f"Healthcare-classified recipient ({e.healthcare_basis}) has no active Type-2 (organizational) NPI in NPPES "
            f"matching its name in {e.address.state or 'any state'} after {len(npi.query_names)} query variant(s) and "
            f"{npi.candidates_examined} candidate record(s). Unverified provider-registration discrepancy.",
            ev,
        )

    # -- R4 ---------------------------------------------------------------- #

    def _rule_colocation(self, e: AwardeeEntity, d: EnrichmentData, ctx: ScoringContext) -> RuleEvaluation:
        key = e.address.location_key
        if not key:
            return self._ev(RULE_COLOCATION, RuleStatus.INSUFFICIENT_DATA, "Address of record lacks a street/ZIP for exact matching.")
        members = ctx.by_location.get(key, [])
        groups: dict[str, list[str]] = defaultdict(list)
        for k in members:
            groups[ctx.affiliation_group(k)].append(k)
        unrelated = len(groups)
        others = [k for k in members if k != e.entity_key]
        ev = self._evidence(
            sorted({r for k in members for r in ctx.entities[k].source_record_ids[-1:]}),
            "address.location_key",
            {"location_key": key, "entities": [ctx.entities[k].name for k in members], "unrelated_groups": unrelated},
            note="Co-location is measured only among recipients returned by this scan.",
        )
        if unrelated >= self.config.colocation_min_unrelated:
            return self._ev(
                RULE_COLOCATION, RuleStatus.TRIGGERED,
                f"{len(members)} awardees ({unrelated} with no shared UEI/parent or similar name) list the identical "
                f"address of record '{key.replace('|', ' ')}'. Anomalous co-location pattern.",
                ev, others,
            )
        return self._ev(RULE_COLOCATION, RuleStatus.NOT_TRIGGERED,
                        f"{unrelated} unrelated awardee group(s) at this exact address (threshold: {self.config.colocation_min_unrelated}).",
                        ev if others else [], others)

    # -- R5 ---------------------------------------------------------------- #

    def _rule_exec_dup(self, e: AwardeeEntity, d: EnrichmentData, ctx: ScoringContext) -> RuleEvaluation:
        if not e.officers:
            return self._ev(RULE_EXEC_DUP, RuleStatus.INSUFFICIENT_DATA,
                            "No officers/authorized officials published for this recipient (USASpending executive "
                            "compensation or NPPES authorized official).")
        own_group = ctx.affiliation_group(e.entity_key)
        locality = e.address.locality_key
        hits: list[tuple[str, list[str]]] = []
        evidence: list[EvidenceItem] = []
        for off in e.officers:
            linked = []
            for other_key, listed_as in ctx.by_officer.get(off.normalized_name, []):
                if other_key == e.entity_key:
                    continue
                other = ctx.entities[other_key]
                if ctx.affiliation_group(other_key) == own_group:
                    continue
                if locality and other.address.locality_key != locality:
                    continue
                linked.append(other_key)
                evidence += self._evidence(
                    [r for o in other.officers if o.normalized_name == off.normalized_name for r in o.source_record_ids][:1],
                    f"{other.name}.officers", listed_as,
                    note="Name-only match; identity of the individual is not confirmed.",
                )
            if linked:
                hits.append((off.name, sorted(set(linked))))
                evidence = self._evidence(off.source_record_ids[:1], f"{e.name}.officers ({off.role_source})", off.name) + evidence
        if hits:
            n_entities = 1 + len({k for _, ks in hits for k in ks})
            if n_entities >= self.config.exec_min_disconnected_entities:
                desc = "; ".join(f"'{name}' also listed on {len(ks)} other entit{'y' if len(ks) == 1 else 'ies'}" for name, ks in hits)
                return self._ev(
                    RULE_EXEC_DUP, RuleStatus.TRIGGERED,
                    f"{desc} with no shared UEI/parent or similar name, in the same locality "
                    f"({(locality or 'unknown').replace('|', ', ')}). Anomalous officer-overlap pattern.",
                    evidence, sorted({k for _, ks in hits for k in ks}),
                )
        return self._ev(RULE_EXEC_DUP, RuleStatus.NOT_TRIGGERED,
                        f"None of {len(e.officers)} listed officer(s) appear on a disconnected awardee in the same locality.")
