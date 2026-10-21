import json
from datetime import date

import httpx

from scrimmy.clients import SocrataClient
from scrimmy.licensing import LicenseLookup, LicenseSource, autodetect_fields, load_license_sources
from scrimmy.math_engine import CapacityEngine
from scrimmy.models import Address, AwardeeEntity, AwardRecord, LicenseLookupResult, LicenseRecord
from scrimmy.normalize import normalize_address, normalize_entity_name


def entity(amount=1_200_000, start=date(2024, 10, 1), end=date(2025, 9, 30), program="10.558", name="Sunrise Learning Center LLC"):
    return AwardeeEntity(
        entity_key="G", name=name, normalized_name=normalize_entity_name(name),
        address=normalize_address(Address(line1="300 Example Ave", city="X", state="MN", zip5="55990")),
        awards=[AwardRecord(award_id="G1", award_group="grants", amount=amount, start_date=start, end_date=end, program_numbers=[program])],
    )


def lic(capacity):
    return LicenseLookupResult(performed=True, best_match=LicenseRecord(source="t", facility_name="SUNRISE", capacity=capacity))


def test_capacity_formula_is_exact():
    res = CapacityEngine().analyze(entity(), lic(40), None)
    assert res.performed and res.benchmark_key == "child_nutrition_meal" and res.capacity_basis == "state_license"
    # $1.2M / ($4.50 x 2 meals x 365 days) = 365.30 children/day; / 40 licensed = 9.133x
    assert res.annualized_disbursement == 1_200_000 and res.estimated_daily_beneficiaries == 365.3 and res.capacity_ratio == 9.133


def test_hospice_per_diem_and_type_ceiling():
    e = entity(amount=153_300_000, program="", name="Example Hospice LLC")
    e.awards[0].program_numbers = []
    e.awards[0].naics_code = "623110"
    res = CapacityEngine().analyze(e, LicenseLookupResult(), None)
    # $153.3M / ($210 x 1 x 365) = 2,000 patients/day vs 500-patient facility-type ceiling = 4.0x
    assert res.benchmark_key == "hospice_per_diem" and res.capacity_basis == "facility_type_ceiling"
    assert res.estimated_daily_beneficiaries == 2000.0 and res.capacity_ratio == 4.0


def test_multi_year_awards_are_annualized():
    res = CapacityEngine().analyze(entity(amount=2_100_000, start=date(2023, 10, 1), end=date(2026, 9, 30)), lic(400), None)
    assert round(res.period_years) == 3 and res.annualized_disbursement == round(2_100_000 / res.period_years, 2)


def test_missing_inputs_are_reported():
    no_ceiling = CapacityEngine(use_type_ceiling=False)
    assert "No matched state license" in no_ceiling.analyze(entity(), LicenseLookupResult(performed=True), None).reason_not_performed
    ccdf = CapacityEngine().analyze(entity(program="93.575"), lic(40), None)
    assert not ccdf.performed and "--unit-rate child_care_subsidy_day" in ccdf.reason_not_performed
    overridden = CapacityEngine().with_rate_overrides({"child_care_subsidy_day": 50.0}).analyze(entity(program="93.575"), lic(40), None)
    assert overridden.performed and overridden.unit_rate == 50.0


def test_autodetect_fields():
    cols = ["operation_id", "operation_name", "operation_type", "address_line", "city", "zip_code", "total_capacity", "operation_status"]
    f = autodetect_fields(cols)
    assert f == {"name": "operation_name", "address": "address_line", "city": "city", "zip": "zip_code", "capacity": "total_capacity",
                 "status": "operation_status", "license_type": "operation_type", "license_number": "operation_id"}


def test_socrata_lookup_matches_and_records_provenance(make_http):
    seen = []

    def handler(req):
        seen.append(str(req.url))
        if req.url.path.startswith("/api/views/"):
            return httpx.Response(200, json={"columns": [{"fieldName": c} for c in ["facility_name", "zip", "licensed_capacity", "street_address"]]})
        return httpx.Response(200, json=[{"facility_name": "SUNRISE LEARNING CENTER", "zip": "55990", "licensed_capacity": "40",
                                          "street_address": "300 EXAMPLE AVENUE"}])

    http = make_http(httpx.MockTransport(handler))
    lookup = LicenseLookup([LicenseSource(key="t", kind="socrata", states=["MN"], domain="data.example.gov", dataset_id="abcd-1234")], SocrataClient(http))
    res = lookup.lookup(entity(), None)
    assert res.best_match and res.best_match.capacity == 40 and res.best_match.address_match
    assert "starts_with(zip, '55990')" in seen[1].replace("%28", "(").replace("%29", ")").replace("%2C", ",").replace("+", " ").replace("%27", "'")
    assert len(res.source_record_ids) == 2


def test_csv_source(tmp_path):
    (tmp_path / "lic.csv").write_text("Facility Name,Zip,Capacity,License Type\nSUNRISE LEARNING CENTER,55990,40,Center\n")
    (tmp_path / "sources.json").write_text(json.dumps({"sources": [{"key": "csv", "kind": "csv", "states": ["MN"], "path": "lic.csv"}]}))
    lookup = LicenseLookup(load_license_sources(tmp_path / "sources.json"), None)
    res = lookup.lookup(entity(), None)
    assert res.best_match.capacity == 40 and res.best_match.source == "operator_csv:lic.csv"
    assert lookup.lookup(entity().model_copy(update={"address": Address(state="WI")}), None).performed is False
