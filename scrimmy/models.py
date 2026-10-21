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
    data_gaps: list[str] = Field(default_factory=list, description="Lookups that failed; the entity was scored without them.")
    source_record_ids: list[str] = Field(default_factory=list)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def total_awarded(self) -> float:
        return round(sum(a.amount for a in self.awards), 2)

    @property
    def has_award_financials(self) -> bool:
        """Core award data needed for scoring: at least one award with a positive obligated amount."""
        return any(a.amount > 0 for a in self.awards)


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
    street_number_match: bool = False
    match_method: str | None = Field(default=None, description="'name_search', 'postal_street_number' or 'postal_fuzzy_name'.")


class NPIMatchResult(StrictModel):
    performed: bool = False
    query_names: list[str] = Field(default_factory=list)
    postal_code_searched: str | None = None
    postal_candidates: int = 0
    postal_sweep_truncated: bool = False
    stages: list[str] = Field(default_factory=list, description="Human-readable log of each lookup stage and its outcome.")
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
    state_fips: str | None = None
    county_fips: str | None = Field(default=None, description="5-digit state+county FIPS from the Census geographies response.")
    county_name: str | None = None
    census_tract: str | None = Field(default=None, description="11-digit census tract GEOID.")
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


class StrikeForceTarget(StrictModel):
    """Where a recipient falls relative to the selected DOJ Health Care Fraud Strike Force preset."""

    hub_key: str
    hub_label: str
    in_focus_area: bool = Field(description="True if the recipient's county or state is one the preset targets.")
    district: str | None = Field(default=None, description="Federal judicial district, e.g. 'Southern District of Florida'.")
    us_attorney_office: str | None = None
    county_name: str | None = None
    county_fips: str | None = None
    basis: str = Field(description="How the district was determined, e.g. 'Census county FIPS 12086'.")


class LEIEMatchStrength(StrEnum):
    CONFIRMED_IDENTIFIER = "confirmed_identifier"  # exact NPI match
    STRONG = "strong"                              # name + state + (city/ZIP or middle initial) agreement
    POSSIBLE = "possible"                          # name + state only; reported, never scored


class LEIEMatch(StrictModel):
    subject: str = Field(description="Who/what in our data matched: entity name or officer name.")
    subject_role: str = Field(description="'entity' or the officer's role_source.")
    strength: LEIEMatchStrength
    similarity: float
    matched_on: list[str] = Field(default_factory=list, description="Fields that agreed, e.g. ['name', 'state', 'zip'].")
    leie_row: dict[str, str] = Field(description="The LEIE row exactly as published (all columns).")
    leie_row_number: int
    exclusion_type: str | None = None
    exclusion_date: date | None = None


class LEIEScreenResult(StrictModel):
    performed: bool = False
    dataset_path: str | None = None
    dataset_sha256: str | None = None
    dataset_rows: int = 0
    subjects_screened: list[str] = Field(default_factory=list)
    matches: list[LEIEMatch] = Field(default_factory=list)
    error: str | None = None
    source_record_ids: list[str] = Field(default_factory=list)


class LicenseRecord(StrictModel):
    source: str = Field(description="e.g. 'socrata:data.texas.gov/bc5r-88dy' or 'operator_csv:licenses.csv'.")
    license_number: str | None = None
    facility_name: str
    license_type: str | None = None
    status: str | None = None
    capacity: int | None = None
    address: Address | None = None
    name_similarity: float = 0.0
    address_match: bool = False
    raw: dict[str, Any] = Field(default_factory=dict)
    source_record_ids: list[str] = Field(default_factory=list)


class LicenseLookupResult(StrictModel):
    performed: bool = False
    sources_queried: list[str] = Field(default_factory=list)
    candidates_examined: int = 0
    best_match: LicenseRecord | None = None
    error: str | None = None
    source_record_ids: list[str] = Field(default_factory=list)


class CapacityAnalysis(StrictModel):
    """Deterministic capacity-to-disbursement arithmetic. Every input is recorded so the result can be recomputed by hand."""

    performed: bool = False
    benchmark_key: str | None = None
    benchmark_label: str | None = None
    unit_rate: float | None = Field(default=None, description="Assumed federal reimbursement per beneficiary-day (USD).")
    operating_days_per_year: int | None = None
    benchmark_citation: str | None = None
    units_per_beneficiary_day: float | None = Field(default=None, description="Reimbursable units per beneficiary per day (e.g. meals).")
    capacity_basis: str | None = Field(default=None, description="'state_license' (matched licensed capacity) or 'facility_type_ceiling'.")
    award_total: float | None = None
    period_start: date | None = None
    period_end: date | None = None
    period_years: float | None = None
    annualized_disbursement: float | None = None
    estimated_daily_beneficiaries: float | None = None
    licensed_capacity: int | None = None
    capacity_ratio: float | None = Field(default=None, description="estimated_daily_beneficiaries / licensed_capacity")
    ratio_threshold: float | None = None
    formula: str | None = None
    reason_not_performed: str | None = None


class VisionCategory(StrEnum):
    SINGLE_FAMILY_RESIDENCE = "SINGLE_FAMILY_RESIDENCE"
    COMMERCIAL_FACILITY_WITH_SIGNAGE = "COMMERCIAL_FACILITY_WITH_SIGNAGE"
    COMMERCIAL_MAIL_RECEIVER_OR_PO_BOX = "COMMERCIAL_MAIL_RECEIVER_OR_PO_BOX"
    VACANT_LOT = "VACANT_LOT"
    INDUSTRIAL_WAREHOUSE = "INDUSTRIAL_WAREHOUSE"
    INDETERMINATE = "INDETERMINATE"


class VisionAssessment(StrictModel):
    category: VisionCategory
    confidence: float = Field(ge=0.0, le=1.0)
    physical_markers: list[str] = Field(default_factory=list)
    rationale: str = ""


class ImageryRecord(StrictModel):
    provider: str = Field(description="'mapillary' or 'google_street_view'.")
    image_id: str | None = Field(default=None, description="Mapillary image id or Google pano_id.")
    captured_at: date | None = None
    camera_lat: float | None = None
    camera_lon: float | None = None
    heading_deg: float | None = None
    distance_m: float | None = None
    request_url: str | None = Field(default=None, description="Image URL with any API key redacted.")
    image_sha256: str | None = None
    stored_locally: bool = Field(default=False, description="False for Google imagery (Maps Platform terms restrict storage); hash + pano_id kept instead.")
    license_note: str | None = None
    source_record_ids: list[str] = Field(default_factory=list)


class VisionVerification(StrictModel):
    performed: bool = False
    imagery: ImageryRecord | None = None
    assessment: VisionAssessment | None = None
    model: str | None = None
    raw_model_output: str | None = None
    error: str | None = None


class EnrichmentData(StrictModel):
    npi: NPIMatchResult = Field(default_factory=NPIMatchResult)
    geocode: GeocodeResult = Field(default_factory=GeocodeResult)
    land_use: LandUseResult = Field(default_factory=LandUseResult)
    leie: LEIEScreenResult = Field(default_factory=LEIEScreenResult)
    license: LicenseLookupResult = Field(default_factory=LicenseLookupResult)
    capacity: CapacityAnalysis = Field(default_factory=CapacityAnalysis)
    vision: VisionVerification = Field(default_factory=VisionVerification)
    strike_force: StrikeForceTarget | None = None
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
    scoring_profile: str = "registry"
    strike_force: str | None = Field(default=None, description="Strike Force preset key used for targeting, if any.")
    nationwide: bool = False
    tool_version: str = ""


class NarrativeProvenance(StrictModel):
    backend: str = Field(description="'template' or the LLM provider/model used for prose sections.")
    model: str | None = None
    fell_back_to_template: bool = False
    fallback_reason: str | None = None
    generated_at: datetime = Field(default_factory=utcnow)


class NetworkSummary(StrictModel):
    """The entity's neighbourhood in the officer/entity/address graph."""

    component_entities: list[str] = Field(default_factory=list, description="Entity keys in the same connected component.")
    shared_addresses: list[dict[str, Any]] = Field(default_factory=list)
    shared_people: list[dict[str, Any]] = Field(default_factory=list)
    component_total_awarded: float = 0.0
    dot: str | None = Field(default=None, description="Graphviz DOT rendering of the component.")


class TrackAssessment(StrictModel):
    """Deterministic routing suggestion for the reviewer. Counsel makes the actual decision."""

    track_a_criminal_referral: bool
    track_b_civil_fca: bool
    primary: str
    rationale: list[str] = Field(default_factory=list)


class EvidenceArtifact(StrictModel):
    path: str = Field(description="Path relative to the lead's evidence directory.")
    sha256: str
    bytes: int
    kind: str = Field(description="'api_response', 'image', 'dataset_extract', 'ledger'.")
    source_record_id: str | None = None
    source_url: str | None = None
    retrieved_at: datetime | None = None


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
    network: NetworkSummary = Field(default_factory=NetworkSummary)
    track: TrackAssessment | None = None
    evidence_dir: str | None = None
    evidence_manifest: list[EvidenceArtifact] = Field(default_factory=list)
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
