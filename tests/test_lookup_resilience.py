"""Regression tests: NPPES false 'no active NPI', fragile recipient enrichment, and blocked OSM map tiles."""

import json
from datetime import date
from pathlib import Path

import httpx
import pytest

from scrimmy.clients import NPPESClient, SourceAPIError, USASpendingClient, nppes_search_names
from scrimmy.ingestion import TargetCollector
from scrimmy.models import Address, AwardeeEntity, AwardRecord, EnrichmentData, RuleStatus
from scrimmy.normalize import normalize_address, normalize_entity_name
from scrimmy.scorer import PHANTOM_HEALTHCARE_PROVIDER, DeterministicScorer, ScoringContext

ROOT = Path(__file__).parents[1]

# The reported false positive: NPPES has this organization, but not under the name as USASpending spells it.
NAME = "ST. ANTHONY PARK HOME, INC."
NPI = "1679577118"


def entity(name=NAME, line1="2237 Commonwealth Ave", zip5="55108", state="MN"):
    return AwardeeEntity(
        entity_key="E", name=name, normalized_name=normalize_entity_name(name),
        address=normalize_address(Address(line1=line1, city="Saint Paul", state=state, zip5=zip5)),
        awards=[AwardRecord(award_id="A1", award_group="grants", amount=300_000, date_signed=date(2025, 1, 2))],
        is_healthcare=True, healthcare_basis="test",
    )


def org(npi, name, address_1, postal="551081234", status="A", other_names=()):
    return {"number": npi, "enumeration_type": "NPI-2",
            "basic": {"organization_name": name, "status": status, "enumeration_date": "2007-05-01"},
            "other_names": [{"organization_name": n} for n in other_names],
            "addresses": [{"address_purpose": "LOCATION", "address_1": address_1, "city": "SAINT PAUL", "state": "MN",
                           "postal_code": postal, "country_code": "US"}],
            "practiceLocations": [], "taxonomies": []}


def nppes(make_http, by_name=None, by_zip=None, zip_status=200):
    """Fake NPPES: organization_name queries -> by_name (default none), postal_code sweeps -> by_zip, paged."""
    seen = []

    def handler(req):
        p = req.url.params
        seen.append(dict(p))
        if p.get("organization_name"):
            return httpx.Response(200, json={"result_count": 0, "results": (by_name or {}).get(p["organization_name"], [])})
        if p.get("postal_code"):
            if zip_status != 200:
                return httpx.Response(zip_status, json={"error": "upstream"})
            skip, limit = int(p.get("skip", 0)), int(p["limit"])
            page = (by_zip or [])[skip:skip + limit]
            return httpx.Response(200, json={"result_count": len(page), "results": page})
        return httpx.Response(200, json={"Errors": [{"description": "No valid search criteria provided"}]})

    client = NPPESClient(make_http(httpx.MockTransport(handler), max_attempts=1))
    return client, seen


# --------------------------------------------------------------------------- #
# NPPES name cleaning + multi-stage matching
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("raw,expected_first", [
    ("ST. ANTHONY PARK HOME, INC.", "ST ANTHONY PARK HOME"),
    ("Acme Home-Health Care, L.L.C.", "ACME HOME HEALTH CARE"),
    ("Twin Cities Hospice/Palliative Co", "TWIN CITIES HOSPICE PALLIATIVE"),
    ("A.B.C. Clinic P.C.", "ABC CLINIC"),
    ("Smith & Jones P.A.", "SMITH & JONES"),
])
def test_nppes_search_names(raw, expected_first):
    terms = nppes_search_names(raw)
    assert terms[0] == expected_first
    assert all(not t.endswith((" INC", " LLC", " CORP", " CO", " LTD", " PC", " PA")) for t in terms)


def test_st_saint_variants():
    assert nppes_search_names(NAME) == ["ST ANTHONY PARK HOME", "ST. ANTHONY PARK HOME", "SAINT ANTHONY PARK HOME"]


def test_zip_fallback_finds_npi_by_street_number(make_http):
    registered = org(NPI, "ST ANTHONY PARK HOME INC", "2237 COMMONWEALTH AVE")
    neighbour = org("1999999999", "COMMONWEALTH DENTAL ASSOCIATES", "2237 COMMONWEALTH AVE")  # same building, unrelated
    client, seen = nppes(make_http, by_zip=[neighbour, registered])
    res = client.get_npi(entity())
    assert res.active_npi_found and res.best_match.npi == NPI and res.best_match.match_method == "postal_street_number"
    assert [m.npi for m in res.matches] == [NPI]  # the unrelated tenant at the same address is not accepted
    assert res.postal_code_searched == "55108" and res.error is None
    assert seen[0]["organization_name"] == "ST ANTHONY PARK HOME*" and seen[0]["state"] == "MN"
    assert any(p.get("postal_code") == "55108" and p.get("enumeration_type") == "NPI-2" for p in seen)


def test_zip_fallback_fuzzy_name_without_street_match(make_http):
    elsewhere = org(NPI, "SAINT ANTHONY PARK HOME", "1 OTHER ST")
    client, _ = nppes(make_http, by_zip=[elsewhere])
    res = client.get_npi(entity())
    assert res.active_npi_found and res.best_match.match_method == "postal_fuzzy_name"


def test_name_stage_match_skips_zip_sweep(make_http):
    client, seen = nppes(make_http, by_name={"ST ANTHONY PARK HOME*": [org(NPI, "ST. ANTHONY PARK HOME", "2237 COMMONWEALTH AVE")]})
    res = client.get_npi(entity())
    assert res.best_match.match_method == "name_search"
    assert not any("postal_code" in p for p in seen)


def test_no_npi_only_after_both_stages_complete(make_http):
    client, _ = nppes(make_http, by_zip=[org("1999999999", "COMMONWEALTH DENTAL ASSOCIATES", "9 ELM ST")])
    res = client.get_npi(entity())
    assert not res.active_npi_found and res.error is None and res.postal_candidates == 1
    assert any("address fallback ZIP 55108" in s for s in res.stages)


@pytest.mark.parametrize("case", ["zip_fails", "no_zip", "truncated"])
def test_incomplete_lookup_is_insufficient_not_a_finding(make_http, case):
    if case == "zip_fails":
        client, _ = nppes(make_http, zip_status=500)
        e = entity()
    elif case == "no_zip":
        client, _ = nppes(make_http)
        e = entity(zip5=None)
    else:
        many = [org(f"1{i:09d}", f"UNRELATED PRACTICE {i}", f"{i} MAIN ST") for i in range(1300)]
        client, _ = nppes(make_http, by_zip=many)
        e = entity()
    res = client.get_npi(e)
    assert not res.active_npi_found and res.error
    d = EnrichmentData(npi=res)
    bd = DeterministicScorer().score(e, d, ScoringContext.build([(e, d)]))
    rule = next(ev for ev in bd.evaluations if ev.rule_id == PHANTOM_HEALTHCARE_PROVIDER)
    assert rule.status is RuleStatus.INSUFFICIENT_DATA and rule.points_awarded == 0


def test_zip_sweep_paginates(make_http):
    many = [org(f"1{i:09d}", f"UNRELATED PRACTICE {i}", f"{i} MAIN ST") for i in range(450)]
    client, seen = nppes(make_http, by_zip=many)
    res = client.get_npi(entity())
    skips = [int(p["skip"]) for p in seen if "postal_code" in p]
    assert skips == [0, 200, 400] and res.postal_candidates == 450 and not res.postal_sweep_truncated


# --------------------------------------------------------------------------- #
# Graceful USASpending recipient enrichment
# --------------------------------------------------------------------------- #

ROW = {"Award ID": "G1", "Recipient Name": NAME, "Recipient UEI": "ABCDEFGH1234", "recipient_id": "hash-C",
       "Award Amount": 410000.0, "Start Date": "2025-01-02", "End Date": "2026-01-01", "Awarding Agency": "HHS",
       "Award Type": "PROJECT GRANT (B)", "generated_internal_id": "ASST_NON_G1",
       "Recipient Location": {"address_line1": "2237 COMMONWEALTH AVE", "address_line2": None, "city_name": "SAINT PAUL",
                              "state_code": "MN", "zip5": "55108", "zip4": "1234", "county_name": "RAMSEY",
                              "location_country_code": "USA"}}


@pytest.mark.parametrize("profile_status", [404, 400])
def test_profile_failure_falls_back_to_award_row(make_http, profile_status):
    def handler(req):
        if req.url.path.endswith("/spending_by_award/"):
            body = json.loads(req.content)
            assert "Recipient Location" in body["fields"]
            rows = [ROW] if "02" in body["filters"]["award_type_codes"] else []
            return httpx.Response(200, json={"results": rows, "page_metadata": {"hasNext": False}})
        if "/recipient/" in req.url.path:
            return httpx.Response(profile_status, json={"detail": "Recipient not found"})
        return httpx.Response(400, json={"detail": "bad award id"})

    from scrimmy.clients import AwardSearchFilters

    collector = TargetCollector(USASpendingClient(make_http(httpx.MockTransport(handler), max_attempts=1)))
    [e] = collector.collect(AwardSearchFilters(state="MN"), ["grants"], 10)
    assert e.uei == "ABCDEFGH1234" and e.total_awarded == 410000.0
    assert e.address.location_key == "2237 COMMONWEALTH AVE|55108" and e.address.county == "RAMSEY"
    assert any("recipient profile unavailable" in g for g in e.data_gaps)
    assert any("award detail G1 unavailable" in g for g in e.data_gaps)
    assert e.has_award_financials


def test_time_budget_stops_retries(make_http):
    import time as _time

    calls = []
    http = make_http(httpx.MockTransport(lambda req: calls.append(1) or httpx.Response(503)), max_attempts=5)
    http.deadline = _time.monotonic() + 0.5  # less than the minimum attempt budget
    with pytest.raises(SourceAPIError, match="time budget exhausted"):
        http.request_json("t", "GET", "https://example.test/")
    assert calls == []


def test_enrichment_stage_failure_is_isolated(monkeypatch):
    from scrimmy.enrichment import CrossReferencer

    cross = CrossReferencer.__new__(CrossReferencer)
    cross.vertical = None
    cross.formation_index = None
    cross.sam = None
    cross.leie = None
    cross.licenses = None
    cross.capacity = None
    cross.config = __import__("scrimmy.enrichment", fromlist=["EnrichmentConfig"]).EnrichmentConfig()
    cross._locate = lambda address: (_ for _ in ()).throw(RuntimeError("census exploded"))
    cross._npi = lambda e: (_ for _ in ()).throw(KeyError("basic"))
    e = entity()
    d = cross.enrich(e)
    assert any("geocoding / land use failed" in n for n in d.notes) and any("NPPES lookup failed" in n for n in d.notes)
    assert len(e.data_gaps) == 2
    bd = DeterministicScorer().score(e, d, ScoringContext.build([(e, d)]))
    assert bd.score >= 0  # still scored


# --------------------------------------------------------------------------- #
# Map tiles
# --------------------------------------------------------------------------- #

def test_hosted_map_uses_carto_not_osm_tile_servers():
    js = (ROOT / "public" / "app.js").read_text()
    assert "tile.openstreetmap.org/{z}" not in js  # no requests to the OSM tile servers (the comment may name them)
    assert "https://{s}.basemaps.cartocdn.com/" in js and "light_all" in js and "{r}.png" in js
    assert 'href="https://carto.com/">CARTO</a>' in js and "openstreetmap.org/copyright" in js
