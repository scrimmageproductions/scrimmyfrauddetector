"""Deterministic capacity-to-disbursement arithmetic.

    annualized_disbursement       = award_total / max(1, period_years)
    estimated_daily_beneficiaries = annualized_disbursement / (unit_rate * operating_days_per_year)
    capacity_ratio                = estimated_daily_beneficiaries / licensed_capacity

The rule flags when ``capacity_ratio > ratio_threshold`` (default 2.0).

This is an *indicator*, not proof. The benchmarks are approximate national rates
that the operator should confirm against the program's current published rates
(for CACFP, the annual USDA reimbursement-rate notice in the Federal Register;
for hospice, the CMS Hospice Wage Index final rule). The arithmetic assumes the
whole award paid for beneficiary-days at the matched licensed site. That assumption
fails for sponsors and pass-through recipients that fund many sites, for awards
covering administration or capital, and for multi-year obligations not yet spent.
Each dossier says so.

Annualising over at least one year is deliberately conservative: a short award is
never inflated into a larger yearly rate.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .models import AwardeeEntity, AwardRecord, CapacityAnalysis, LicenseLookupResult


@dataclass(frozen=True)
class Benchmark:
    key: str
    label: str
    unit_rate: float | None
    operating_days_per_year: int
    citation: str
    program_numbers: tuple[str, ...] = ()
    naics_prefixes: tuple[str, ...] = ()
    verticals: tuple[str, ...] = ()


DEFAULT_BENCHMARKS: dict[str, Benchmark] = {
    "cacfp_child_day": Benchmark(
        key="cacfp_child_day",
        label="CACFP child care center: breakfast + lunch + snack per child-day (approx. free-tier, contiguous U.S.)",
        unit_rate=8.40,
        operating_days_per_year=250,
        citation="USDA FNS CACFP reimbursement rates (annual Federal Register notice). Approximate; confirm the current school-year rates.",
        program_numbers=("10.558",),
        naics_prefixes=("624210",),
    ),
    "ccdf_child_day": Benchmark(
        key="ccdf_child_day",
        label="CCDF child care subsidy per child-day (state-specific; operator must supply)",
        unit_rate=None,
        operating_days_per_year=250,
        citation="State CCDF market-rate survey / maximum payment rates. No national default: set with --unit-rate ccdf_child_day=<USD>.",
        program_numbers=("93.575", "93.596"),
        naics_prefixes=("624410",),
        verticals=("childcare",),
    ),
    "hospice_patient_day": Benchmark(
        key="hospice_patient_day",
        label="Hospice routine home care per patient-day (approx. national midpoint)",
        unit_rate=220.00,
        operating_days_per_year=365,
        citation="CMS Hospice Wage Index final rule (routine home care per diem). Approximate; confirm the current fiscal year rate.",
        naics_prefixes=("623110", "624120"),
        verticals=("hospice",),
    ),
}


@dataclass
class CapacityEngine:
    benchmarks: dict[str, Benchmark] = field(default_factory=lambda: dict(DEFAULT_BENCHMARKS))
    ratio_threshold: float = 2.0

    def with_rate_overrides(self, overrides: dict[str, float]) -> CapacityEngine:
        b = dict(self.benchmarks)
        for key, rate in overrides.items():
            if key not in b:
                raise ValueError(f"unknown benchmark {key!r}; known: {', '.join(b)}")
            old = b[key]
            b[key] = Benchmark(old.key, old.label, rate, old.operating_days_per_year,
                               f"Operator-supplied rate ({rate}); default citation: {old.citation}",
                               old.program_numbers, old.naics_prefixes, old.verticals)
        return CapacityEngine(b, self.ratio_threshold)

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
        res.award_total = round(sum(a.amount for a in awards), 2)
        starts = [a.effective_date for a in awards if a.effective_date]
        ends = [a.end_date for a in awards if a.end_date]
        res.period_start = min(starts) if starts else None
        res.period_end = max(ends) if ends else None
        if res.period_start and res.period_end and res.period_end > res.period_start:
            res.period_years = round((res.period_end - res.period_start).days / 365.25, 3)
        else:
            res.period_years = 1.0
        lic = license_result.best_match
        if bench.unit_rate is None:
            res.reason_not_performed = f"Benchmark '{bench.key}' has no unit rate; supply --unit-rate {bench.key}=<USD>."
            return res
        if not lic or not lic.capacity:
            res.reason_not_performed = ("No matched state license with a published capacity."
                                        if license_result.performed else "No state licensing source configured for this state/vertical.")
            return res
        res.performed = True
        res.licensed_capacity = lic.capacity
        years = max(1.0, res.period_years)
        res.annualized_disbursement = round(res.award_total / years, 2)
        res.estimated_daily_beneficiaries = round(res.annualized_disbursement / (bench.unit_rate * bench.operating_days_per_year), 2)
        res.capacity_ratio = round(res.estimated_daily_beneficiaries / lic.capacity, 3)
        res.formula = (
            f"annualized = ${res.award_total:,.2f} / max(1, {res.period_years}) = ${res.annualized_disbursement:,.2f}; "
            f"est. daily beneficiaries = ${res.annualized_disbursement:,.2f} / (${bench.unit_rate:,.2f} x {bench.operating_days_per_year}) "
            f"= {res.estimated_daily_beneficiaries:,.2f}; ratio = {res.estimated_daily_beneficiaries:,.2f} / {lic.capacity} "
            f"= {res.capacity_ratio:,.3f} (threshold > {self.ratio_threshold})"
        )
        return res
