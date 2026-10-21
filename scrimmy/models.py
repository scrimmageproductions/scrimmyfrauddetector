"""Pydantic v2 data schemas for the Scrimmy pipeline.

Every model that carries a finding also carries provenance: the IDs of the
``SourceRecord`` objects (exact URL, timestamp, response hash, raw snippet)
that the finding was derived from. Nothing in a dossier should be traceable
only to "the tool said so".
"""

from __future__ import annotations

import hashlib
from datetime import date, datetime, timezone
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, computed_field, model_validator

SCHEMA_VERSION = "1.0.0"


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_assignment=True, str_strip_whitespace=True)

    @model_validator(mode="before")
    @classmethod
    def _drop_computed_fields(cls, data: Any) -> Any:
        # Computed fields are serialized for readers of the JSON; ignore them when loading a dossier back.
        if isinstance(data, dict) and cls.model_computed_fields:
            return {k: v for k, v in data.items() if k not in cls.model_computed_fields}
        return data


# --------------------------------------------------------------------------- #
# Provenance / chain of custody
# --------------------------------------------------------------------------- #


class SourceRecord(StrictModel):
    """One HTTP exchange with a public data source, captured for auditability."""

    record_id: str = Field(description="Stable ID: sha256 of method+url+body, truncated, plus a sequence suffix.")
    source: str = Field(description="Logical source name, e.g. 'usaspending', 'nppes', 'census_geocoder', 'osm_overpass'.")
    method: str
    url: str = Field(description="Exact URL requested, including the query string.")
    request_body: Any | None = None
    status_code: int | None = None
    retrieved_at: datetime = Field(default_factory=utcnow)
    elapsed_ms: float | None = None
    attempts: int = 1
    response_sha256: str | None = Field(default=None, description="SHA-256 of the full raw response body.")
    raw_snippet: Any | None = Field(default=None, description="Raw JSON (possibly truncated) as returned by the source.")
    truncated: bool = False
    error: str | None = None


class EvidenceItem(StrictModel):
    """A single observed fact supporting (or contextualising) a rule evaluation."""

    source_record_id: str | None = Field(default=None, description="SourceRecord this fact was read from; None if derived.")
    source_url: str | None = None
    retrieved_at: datetime | None = None
    field: str = Field(description="Field or JSON path the value was read from.")
    observed_value: Any = None
    note: str | None = None


# --------------------------------------------------------------------------- #
# Entities
# --------------------------------------------------------------------------- #


class Address(StrictModel):
    line1: str | None = None
    line2: str | None = None
    city: str | None = None
    state: str | None = None
    zip5: str | None = None
    zip4: str | None = None
    county: str | None = None
    country: str | None = "USA"

    # Populated by normalize.normalize_address
    normalized_street: str | None = None
    normalized_unit: str | None = None
    is_po_box: bool = False
    is_pmb: bool = False

    @computed_field  # type: ignore[prop-decorator]
    @property
    def one_line(self) -> str:
        parts = [p for p in (self.line1, self.line2) if p]
        locality = " ".join(p for p in (self.state, self.zip5) if p)
        tail = ", ".join(p for p in (self.city, locality) if p)
        return ", ".join([*parts, tail]) if tail else ", ".join(parts)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def location_key(self) -> str | None:
        """Exact street+unit+ZIP5 key used for co-location matching."""
        if not self.normalized_street or not self.zip5:
            return None
        unit = f" {self.normalized_unit}" if self.normalized_unit else ""
        return f"{self.normalized_street}{unit}|{self.zip5}"

    @computed_field  # type: ignore[prop-decorator]
    @property
    def locality_key(self) -> str | None:
        if self.city and self.state:
            return f"{self.city.upper()}|{self.state.upper()}"
        return None


class Officer(StrictModel):
    name: str
    normalized_name: str
    title: str | None = None
    role_source: str = Field(description="Where the officer was listed, e.g. 'usaspending.executive_details' or 'nppes.authorized_official'.")
    source_record_ids: list[str] = Field(default_factory=list)


class AwardRecord(StrictModel):
    award_id: str | None = None
    generated_internal_id: str | None = None
    award_group: str = Field(description="USASpending award type group: contracts, grants, direct_payments, loans, other.")
    amount: float = 0.0
    start_date: date | None = None
    date_signed: date | None = None
    end_date: date | None = None
    awarding_agency: str | None = None
    awarding_sub_agency: str | None = None
    award_type: str | None = None
    naics_code: str | None = None
    psc_code: str | None = None
    program_numbers: list[str] = Field(default_factory=list, description="Assistance Listing (CFDA) numbers.")
    description: str | None = None
    source_record_ids: list[str] = Field(default_factory=list)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def effective_date(self) -> date | None:
        """Best available 'award received' date: signature date, else period-of-performance start."""
        return self.date_signed or self.start_date


class AwardeeEntity(StrictModel):
    entity_key: str = Field(description="Internal resolution key (UEI if present, else recipient hash, else name+ZIP).")
    name: str
    normalized_name: str
    alternate_names: list[str] = Field(default_factory=list)
    uei: str | None = None
    duns: str | None = None
    cage: str | None = None
    usaspending_recipient_id: str | None = None
    parent_uei: str | None = None
    parent_name: str | None = None
    address: Address = Field(default_factory=Address)
    business_types: list[str] = Field(default_factory=list)
    awards: list[AwardRecord] = Field(default_factory=list)
    officers: list[Officer] = Field(default_factory=list)
    formation_date: date | None = None
    formation_date_source: str | None = Field(default=None, description="e.g. 'sam.entityStartDate', 'operator_csv', 'nppes.enumeration_date (proxy)'.")
    formation_source_record_ids: list[str] = Field(default_factory=list)
    is_healthcare: bool = False
    healthcare_basis: str | None = None
    source_record_ids: list[str] = Field(default_factory=list)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def total_awarded(self) -> float:
        return round(sum(a.amount for a in self.awards), 2)


# --------------------------------------------------------------------------- #
# Enrichment
# --------------------------------------------------------------------------- #


class NPIRecord(StrictModel):
    npi: str
    organization_name: str | None = None
    status: str | None = Field(default=None, description="'A' = active in NPPES.")
    enumeration_type: str | None = None
    enumeration_date: date | None = None
    last_updated: date | None = None
    authorized_official_name: str | None = None
    authorized_official_title: str | None = None
    taxonomies: list[str] = Field(default_factory=list)
    practice_address: Address | None = None
    mailing_address: Address | None = None
    name_similarity: float = 0.0
    address_match: bool = False


class NPIMatchResult(StrictModel):
    performed: bool = False
    query_names: list[str] = Field(default_factory=list)
    candidates_examined: int = 0
    matches: list[NPIRecord] = Field(default_factory=list)
    best_match: NPIRecord | None = None
    active_npi_found: bool = False
    error: str | None = None
    source_record_ids: list[str] = Field(default_factory=list)


class GeocodeResult(StrictModel):
    performed: bool = False
    matched: bool = False
    input_address: str | None = None
    matched_address: str | None = None
    latitude: float | None = None
    longitude: float | None = None
    tiger_line_id: str | None = None
    tiger_side: str | None = None
    error: str | None = None
    source_record_ids: list[str] = Field(default_factory=list)


class LandUseClass(StrEnum):
    RESIDENTIAL = "residential"
    COMMERCIAL = "commercial"
    MIXED = "mixed"
    CMRA_MAIL_DROP = "cmra_mail_drop"
    PO_BOX = "po_box"
    NO_STRUCTURE_MAPPED = "no_structure_mapped"
    UNKNOWN = "unknown"


class LandUseResult(StrictModel):
    performed: bool = False
    classification: LandUseClass = LandUseClass.UNKNOWN
    signals: list[str] = Field(default_factory=list, description="Human-readable reasons behind the classification.")
    nearest_building_tag: str | None = None
    landuse_tags: list[str] = Field(default_factory=list)
    commercial_pois: list[str] = Field(default_factory=list)
    mail_drop_pois: list[str] = Field(default_factory=list)
    search_radius_m: int | None = None
    error: str | None = None
    source_record_ids: list[str] = Field(default_factory=list)


class EnrichmentData(StrictModel):
    npi: NPIMatchResult = Field(default_factory=NPIMatchResult)
    geocode: GeocodeResult = Field(default_factory=GeocodeResult)
    land_use: LandUseResult = Field(default_factory=LandUseResult)
    notes: list[str] = Field(default_factory=list)


# --------------------------------------------------------------------------- #
# Scoring
# --------------------------------------------------------------------------- #


class RuleStatus(StrEnum):
    TRIGGERED = "triggered"
    NOT_TRIGGERED = "not_triggered"
    INSUFFICIENT_DATA = "insufficient_data"
    NOT_APPLICABLE = "not_applicable"


class AnomalyFlag(StrictModel):
    """A triggered heuristic. Only rules with status TRIGGERED become flags."""

    rule_id: str
    title: str
    description: str = Field(description="Objective, non-accusatory description of the observed pattern.")
    score_contribution: int
    evidence: list[EvidenceItem] = Field(default_factory=list)
    related_entity_keys: list[str] = Field(default_factory=list)


class RuleEvaluation(StrictModel):
    """Full audit record of a single rule, triggered or not."""

    rule_id: str
    title: str
    weight: int
    status: RuleStatus
    points_awarded: int
    rationale: str
    evidence: list[EvidenceItem] = Field(default_factory=list)
    related_entity_keys: list[str] = Field(default_factory=list)


class ScoreBreakdown(StrictModel):
    entity_key: str
    evaluations: list[RuleEvaluation]
    flags: list[AnomalyFlag]
    raw_total: int = Field(description="Arithmetic sum of points from triggered rules (uncapped).")
    max_possible: int = Field(description="Sum of all rule weights.")
    score: int = Field(ge=0, le=100, description="min(raw_total, 100).")
    formula: str
    scorer_version: str


# --------------------------------------------------------------------------- #
# Final output
# --------------------------------------------------------------------------- #


class ScanContext(StrictModel):
    """What the operator asked for. Recorded so a reviewer can reproduce the scan."""

    started_at: datetime = Field(default_factory=utcnow)
    state: str | None = None
    zip_codes: list[str] = Field(default_factory=list)
    vertical: str | None = None
    naics_codes: list[str] = Field(default_factory=list)
    psc_codes: list[str] = Field(default_factory=list)
    program_numbers: list[str] = Field(default_factory=list)
    award_groups: list[str] = Field(default_factory=list)
    min_award: float | None = None
    max_award: float | None = None
    start_date: date | None = None
    end_date: date | None = None
    max_awards: int | None = None
    threshold: int = 50
    tool_version: str = ""


class NarrativeProvenance(StrictModel):
    backend: str = Field(description="'template' or the LLM provider/model used for prose sections.")
    model: str | None = None
    fell_back_to_template: bool = False
    fallback_reason: str | None = None
    generated_at: datetime = Field(default_factory=utcnow)


class FraudRiskDossier(StrictModel):
    """Lead dossier for human review. It is NOT a finding of wrongdoing."""

    schema_version: str = SCHEMA_VERSION
    dossier_id: str
    generated_at: datetime = Field(default_factory=utcnow)
    scan: ScanContext
    entity: AwardeeEntity
    enrichment: EnrichmentData
    score: ScoreBreakdown
    related_entities: list[dict[str, Any]] = Field(default_factory=list, description="Minimal summaries of co-located / officer-linked entities.")
    statutory_references: list[dict[str, str]] = Field(default_factory=list)
    ground_truthing_checklist: list[str] = Field(default_factory=list)
    caveats: list[str] = Field(default_factory=list)
    narrative_provenance: NarrativeProvenance
    executive_summary: str
    sources: list[SourceRecord] = Field(default_factory=list, description="Every SourceRecord referenced anywhere in this dossier.")
    disclaimer: str

    @staticmethod
    def make_id(entity_key: str, generated_at: datetime) -> str:
        digest = hashlib.sha256(f"{entity_key}|{generated_at.isoformat()}".encode()).hexdigest()[:12]
        return f"SCRIMMY-{generated_at:%Y%m%d}-{digest.upper()}"
