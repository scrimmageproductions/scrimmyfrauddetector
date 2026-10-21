"""One place that turns operator choices into ``PipelineSettings`` (shared by the CLI and the web app)."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from .clients import AWARD_GROUPS, AwardSearchFilters
from .enrichment import EnrichmentConfig, OperatorFormationIndex
from .leie_matcher import LEIEMatcher
from .licensing import load_license_sources
from .math_engine import CapacityEngine
from .pipeline import CUSTOM_PRESET_KEY, STRIKE_FORCE_PRESETS, PipelineSettings, VisionSettings
from .scorer import ScoringConfig
from .verticals import PRIORITY_VERTICALS, VERTICALS
from .vision_verifier import make_vision_model

DEFAULT_GROUPS = ["contracts", "grants", "direct_payments", "other"]


def fiscal_year_bounds(fy: int) -> tuple[date, date]:
    """Federal fiscal year N runs from Oct 1 of N-1 through Sep 30 of N."""
    return date(fy - 1, 10, 1), date(fy, 9, 30)


@dataclass
class ScanOptions:
    state: str | None = None
    states: list[str] = field(default_factory=list)
    zip_codes: list[str] = field(default_factory=list)
    strike_force: str | None = None  # key of pipeline.STRIKE_FORCE_PRESETS, or "custom"
    nationwide: bool = False
    vertical: str | None = None
    naics: list[str] = field(default_factory=list)
    psc: list[str] = field(default_factory=list)
    programs: list[str] = field(default_factory=list)
    award_groups: list[str] = field(default_factory=lambda: list(DEFAULT_GROUPS))
    min_award: float | None = 250_000.0
    max_award: float | None = None
    start_date: date | None = None
    end_date: date | None = None
    fiscal_year: int | None = None
    recipient_search: str | None = None
    max_awards: int = 200
    detail_awards: int = 3
    threshold: int = 50
    scoring_profile: str = "registry"
    newly_formed_days: int = 120
    major_award_min: float = 100_000.0
    residential_award_min: float = 250_000.0
    capacity_ratio: float = 2.0
    unit_rates: dict[str, float] = field(default_factory=dict)
    output_dir: Path = Path("./dossiers")
    evidence_dir: Path | None = None
    sam_api_key: str | None = field(default_factory=lambda: os.environ.get("SAM_API_KEY"))
    formation_csv: Path | None = None
    formation_proxy_npi: bool = False
    leie_csv: Path | None = None
    license_sources: Path | None = None
    npi_all: bool = False
    osm_radius: int = 40
    skip_osm: bool = False
    overpass_url: str | None = None
    vision: bool = False
    vision_provider: str = "auto"
    vision_model: str | None = None
    timeout: float = 30.0
    max_retries: int = 5

    def validate(self) -> list[str]:
        errors = []
        preset = self.strike_force and self.strike_force != CUSTOM_PRESET_KEY
        if not (self.state or self.states or self.zip_codes or self.recipient_search or preset or self.nationwide):
            errors.append("provide a Strike Force preset, state(s), ZIP code(s), a recipient search, or choose nationwide to bound the scan")
        if preset and self.strike_force not in STRIKE_FORCE_PRESETS:
            errors.append(f"unknown Strike Force preset {self.strike_force!r}; choose from {', '.join(STRIKE_FORCE_PRESETS)} or custom")
        bad = [g for g in self.award_groups if g not in AWARD_GROUPS]
        if bad:
            errors.append(f"unknown award type group(s): {', '.join(bad)}")
        if self.vertical and self.vertical not in VERTICALS:
            errors.append(f"unknown vertical {self.vertical!r}")
        if self.vision and not (os.environ.get("MAPILLARY_TOKEN") or os.environ.get("GOOGLE_MAPS_API_KEY")):
            errors.append("vision checks need MAPILLARY_TOKEN or GOOGLE_MAPS_API_KEY in the environment")
        return errors

    def to_settings(self) -> PipelineSettings:
        vertical = VERTICALS.get(self.vertical) if self.vertical else None
        if vertical:
            naics, psc, programs = vertical.naics_codes, vertical.psc_codes, vertical.program_numbers
        elif not (self.naics or self.psc or self.programs):
            # Default targeting: child day care & nutrition, home health and hospice (NAICS 624410, 624210,
            # 621610, 623110, 624120) plus those verticals' Assistance Listings for grants.
            naics = sorted({c for k in PRIORITY_VERTICALS for c in VERTICALS[k].naics_codes})
            psc, programs = [], sorted({c for k in PRIORITY_VERTICALS for c in VERTICALS[k].program_numbers})
        else:
            naics, psc, programs = [], [], []
        start, end = self.start_date, self.end_date
        if self.fiscal_year:
            start, end = fiscal_year_bounds(self.fiscal_year)
        preset = STRIKE_FORCE_PRESETS.get(self.strike_force or "")
        filters = AwardSearchFilters(
            state=self.state, states=self.states, zip_codes=self.zip_codes,
            locations=preset.location_filters() if preset else [],
            naics_codes=sorted(set(naics) | set(self.naics)), psc_codes=sorted(set(psc) | set(self.psc)),
            program_numbers=sorted(set(programs) | set(self.programs)),
            min_award=self.min_award, max_award=self.max_award, start_date=start, end_date=end,
            recipient_search_text=self.recipient_search,
        )
        vision = None
        if self.vision:
            vision = VisionSettings(model=make_vision_model(self.vision_provider, self.vision_model),
                                    mapillary_token=os.environ.get("MAPILLARY_TOKEN"),
                                    google_api_key=os.environ.get("GOOGLE_MAPS_API_KEY"))
        return PipelineSettings(
            filters=filters, award_groups=self.award_groups, max_awards=self.max_awards, threshold=self.threshold,
            output_dir=self.output_dir, evidence_dir=self.evidence_dir, vertical=vertical, detail_awards_per_entity=self.detail_awards,
            enrichment=EnrichmentConfig(osm_radius_m=self.osm_radius, npi_for_all=self.npi_all,
                                        formation_proxy_npi=self.formation_proxy_npi, skip_osm=self.skip_osm),
            scoring=ScoringConfig.for_profile(self.scoring_profile, newly_formed_max_days=self.newly_formed_days,
                                              major_award_min=self.major_award_min, residential_award_min=self.residential_award_min,
                                              high_priority_threshold=self.threshold),
            sam_api_key=self.sam_api_key,
            formation_index=OperatorFormationIndex.load(self.formation_csv) if self.formation_csv else None,
            leie=LEIEMatcher.from_csv(self.leie_csv) if self.leie_csv else None,
            license_sources=load_license_sources(self.license_sources) if self.license_sources else [],
            capacity=CapacityEngine(ratio_threshold=self.capacity_ratio).with_rate_overrides(self.unit_rates),
            vision=vision, overpass_url=self.overpass_url, timeout=self.timeout, max_attempts=self.max_retries,
            strike_force=preset, nationwide=self.nationwide and not (preset or self.state or self.states or self.zip_codes),
        )
