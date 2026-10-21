"""Deterministic Mathematical Impossibility Engine: capacity-to-disbursement arithmetic.

    annualized_disbursement       = award_total / max(1, period_years)
    estimated_daily_beneficiaries = annualized_disbursement / (unit_rate * units_per_beneficiary_day * operating_days_per_year)
    capacity_ratio                = estimated_daily_beneficiaries / capacity

``capacity`` is the matched state licensed capacity when a licensing source is
configured. Otherwise it is a facility-type plausibility ceiling, the most
beneficiaries a single facility of that type could plausibly serve per day. The
``MATHEMATICAL_IMPOSSIBILITY`` rule triggers when ``capacity_ratio > ratio_threshold``
(default 2.0, i.e. more than double what the site could serve).

Every default is chosen to *under*-estimate headcount, so a flag means the dollars
exceed what is plausible even on generous assumptions:

* the full year (365 days) is assumed to be billable;
* CACFP assumes the maximum two reimbursable meals per child per day, so one child
  is counted as $9.00/day rather than $4.50;
* multi-year awards are spread over their period of performance (never less than a year).

This is an indicator, not proof. The rates are approximate national figures to be
confirmed against the program's published rates (CACFP: the annual USDA notice in the
Federal Register; hospice: the CMS Hospice Wage Index final rule). The arithmetic
assumes the whole award paid for beneficiary-days at one site. Sponsors, multi-site
operators, pass-through recipients, administrative or capital funding, and unspent
obligations all break that assumption, and each dossier says so.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace

from .models import AwardeeEntity, AwardRecord, CapacityAnalysis, LicenseLookupResult


@dataclass(frozen=True)
class Benchmark:
    key: str
    label: str
    unit_rate: float | None
    units_per_beneficiary_day: float
    operating_days_per_year: int
    plausible_max_capacity: int
    citation: str
    program_numbers: tuple[str, ...] = ()
    naics_prefixes: tuple[str, ...] = ()
    verticals: tuple[str, ...] = ()

    @property
    def daily_rate(self) -> float | None:
        return None if self.unit_rate is None else self.unit_rate * self.units_per_beneficiary_day


DEFAULT_BENCHMARKS: dict[str, Benchmark] = {
    "child_nutrition_meal": Benchmark(
        key="child_nutrition_meal",
        label="Child nutrition (CACFP) reimbursement per meal; 2 reimbursable meals per child-day",
        unit_rate=4.50,
        units_per_beneficiary_day=2,
        operating_days_per_year=365,
        plausible_max_capacity=400,
        citation="USDA FNS CACFP reimbursement rates (annual Federal Register notice); ~$4.50 per lunch/supper at the free rate. "
                 "Approximate; confirm the rates for the award period.",
        program_numbers=("10.558", "10.559"),
        naics_prefixes=("624210",),
    ),
    "child_care_subsidy_day": Benchmark(
        key="child_care_subsidy_day",
        label="CCDF child care subsidy per child-day (state-specific; operator must supply the rate)",
        unit_rate=None,
        units_per_beneficiary_day=1,
        operating_days_per_year=365,
        plausible_max_capacity=400,
        citation="State CCDF payment-rate schedule. No national default: set with --unit-rate child_care_subsidy_day=<USD>.",
        program_numbers=("93.575", "93.596"),
        naics_prefixes=("624410",),
        verticals=("childcare",),
    ),
    "hospice_per_diem": Benchmark(
        key="hospice_per_diem",
        label="Hospice routine home care per patient-day (average per diem)",
        unit_rate=210.00,
        units_per_beneficiary_day=1,
        operating_days_per_year=365,
        plausible_max_capacity=500,
        citation="CMS Hospice Wage Index final rule (routine home care per diem); ~$210/day average. Approximate; confirm the rate for the award period.",
        naics_prefixes=("623110", "624120"),
        verticals=("hospice",),
    ),
}


@dataclass
class CapacityEngine:
    benchmarks: dict[str, Benchmark] = field(default_factory=lambda: dict(DEFAULT_BENCHMARKS))
    ratio_threshold: float = 2.0
    use_type_ceiling: bool = True

    def with_rate_overrides(self, overrides: dict[str, float]) -> CapacityEngine:
        b = dict(self.benchmarks)
        for key, rate in overrides.items():
            if key not in b:
                raise ValueError(f"unknown benchmark {key!r}; known: {', '.join(b)}")
            b[key] = replace(b[key], unit_rate=rate, citation=f"Operator-supplied rate ({rate}); default source: {b[key].citation}")
        return CapacityEngine(b, self.ratio_threshold, self.use_type_ceiling)

    def benchmark_for(self, award: AwardRecord, vertical: str | None) -> Benchmark | None:
        for b in self.benchmarks.values():
            if set(award.program_numbers) & set(b.program_numbers):
                return b
        for b in self.benchmarks.values():
            if award.naics_code and b.naics_prefixes and award.naics_code.startswith(b.naics_prefixes):
                return b
        for b in self.benchmarks.values():
            if vertical and vertical in b.verticals:
                return b
        return None

    def analyze(self, entity: AwardeeEntity, license_result: LicenseLookupResult, vertical: str | None) -> CapacityAnalysis:
        res = CapacityAnalysis(ratio_threshold=self.ratio_threshold)
        groups: dict[str, list[AwardRecord]] = {}
        for a in entity.awards:
            b = self.benchmark_for(a, vertical)
            if b:
                groups.setdefault(b.key, []).append(a)
        if not groups:
            res.reason_not_performed = "No award maps to a per-beneficiary benchmark (program/NAICS/vertical)."
            return res
        key = max(groups, key=lambda k: sum(a.amount for a in groups[k]))
        bench, awards = self.benchmarks[key], groups[key]
        res.benchmark_key, res.benchmark_label, res.benchmark_citation = bench.key, bench.label, bench.citation
        res.unit_rate, res.operating_days_per_year = bench.unit_rate, bench.operating_days_per_year
        res.units_per_beneficiary_day = bench.units_per_beneficiary_day
        res.award_total = round(sum(a.amount for a in awards), 2)
        starts = [a.effective_date for a in awards if a.effective_date]
        ends = [a.end_date for a in awards if a.end_date]
        res.period_start = min(starts) if starts else None
        res.period_end = max(ends) if ends else None
        if res.period_start and res.period_end and res.period_end > res.period_start:
            res.period_years = round((res.period_end - res.period_start).days / 365.25, 3)
        else:
            res.period_years = 1.0
        if bench.daily_rate is None:
            res.reason_not_performed = f"Benchmark '{bench.key}' has no unit rate; supply --unit-rate {bench.key}=<USD>."
            return res

        lic = license_result.best_match
        if lic and lic.capacity:
            capacity, res.capacity_basis = lic.capacity, "state_license"
        elif self.use_type_ceiling:
            capacity, res.capacity_basis = bench.plausible_max_capacity, "facility_type_ceiling"
        else:
            res.reason_not_performed = "No matched state license with a published capacity."
            return res

        res.performed = True
        res.licensed_capacity = capacity
        years = max(1.0, res.period_years)
        res.annualized_disbursement = round(res.award_total / years, 2)
        res.estimated_daily_beneficiaries = round(res.annualized_disbursement / (bench.daily_rate * bench.operating_days_per_year), 2)
        res.capacity_ratio = round(res.estimated_daily_beneficiaries / capacity, 3)
        cap_label = "licensed capacity" if res.capacity_basis == "state_license" else "facility-type ceiling"
        res.formula = (
            f"annualized = ${res.award_total:,.2f} / max(1, {res.period_years}) = ${res.annualized_disbursement:,.2f}; "
            f"est. daily beneficiaries = ${res.annualized_disbursement:,.2f} / (${bench.unit_rate:,.2f} x {bench.units_per_beneficiary_day:g} "
            f"x {bench.operating_days_per_year}) = {res.estimated_daily_beneficiaries:,.2f}; ratio = {res.estimated_daily_beneficiaries:,.2f} / "
            f"{capacity} ({cap_label}) = {res.capacity_ratio:,.3f} (threshold > {self.ratio_threshold})"
        )
        return res
