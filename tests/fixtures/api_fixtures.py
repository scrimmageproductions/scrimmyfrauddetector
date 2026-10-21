"""TEST FIXTURES ONLY: an ``httpx.MockTransport`` serving synthetic responses shaped like the real APIs.

Nothing in the ``scrimmy`` package imports this module; production code paths only
ever call the live endpoints. All names, identifiers, people and addresses below are
fictional ("Example" street names, UEIs prefixed ``EXMPL``, ZIP 55990 in a fictional
town). They let the test suite exercise every stage offline and regenerate the
labelled samples in ``examples/``.
"""

from __future__ import annotations

import json
import zlib
from datetime import date
from pathlib import Path
from urllib.parse import parse_qs

import httpx

from scrimmy.clients import AwardSearchFilters
from scrimmy.enrichment import EnrichmentConfig, FormationDateRecord, OperatorFormationIndex
from scrimmy.leie_matcher import LEIEMatcher
from scrimmy.licensing import LicenseSource
from scrimmy.pipeline import PipelineSettings
from scrimmy.scorer import ScoringConfig
from scrimmy.verticals import VERTICALS

CITY, STATE, ZIP = "LAKEVIEW FALLS", "MN", "55990"

# key: (name, uei, address_line1, address_line2, officers, npi_status or None, awards)
# award: (group, award_id, gid, amount, date_signed, agency, naics/program)
ENTITIES = {
    "A": dict(
        name="NORTHFIELD EXAMPLE HOME HEALTH LLC", uei="EXMPLA000001", line1="4410 EXAMPLE LANE", line2=None,
        officers=["Jordan A. Sample"], npi=None,
        awards=[
            ("contracts", "36C26325P0001", "CONT_AWD_36C26325P0001_3600", 1_240_000.00, "2025-03-03", "Department of Veterans Affairs", "621610"),
            ("grants", "EXMPL-GRANT-0001", "ASST_NON_EXMPLGRANT0001_7530", 310_000.00, "2025-04-21", "Department of Health and Human Services", "93.791"),
        ],
    ),
    "B": dict(
        name="RIVERBEND EXAMPLE CARE SERVICES INC", uei="EXMPLB000002", line1="880 EXAMPLE PARKWAY", line2="#210",
        officers=[], npi="A", official=("CASEY", "EXAMPLEWORTH"),
        awards=[("contracts", "36C26324P0002", "CONT_AWD_36C26324P0002_3600", 420_000.00, "2024-06-10", "Department of Veterans Affairs", "621610")],
    ),
    "C": dict(
        name="LAKEVIEW EXAMPLE PERSONAL CARE LLC", uei="EXMPLC000003", line1="200 EXAMPLE BOULEVARD", line2="SUITE 5",
        officers=["SAMPLE, JORDAN"], npi="A", official=("RILEY", "PLACEHOLDER"),
        awards=[("grants", "EXMPL-GRANT-0003", "ASST_NON_EXMPLGRANT0003_7530", 180_000.00, "2024-09-30", "Department of Health and Human Services", "93.044")],
    ),
    "D": dict(
        name="SUMMIT EXAMPLE WELLNESS LLC", uei="EXMPLD000004", line1="880 Example Pkwy", line2="Suite 210",
        officers=[], npi=None,
        awards=[("grants", "EXMPL-GRANT-0004", "ASST_NON_EXMPLGRANT0004_7530", 275_000.00, "2025-02-14", "Department of Health and Human Services", "93.044")],
    ),
    "E": dict(
        name="CEDAR EXAMPLE HEALTH PARTNERS", uei="EXMPLE000005", line1="880 EXAMPLE PKWY", line2="UNIT 210",
        officers=[], npi="A", official=("TAYLOR", "SAMPLETON"),
        awards=[("contracts", "36C26325P0005", "CONT_AWD_36C26325P0005_3600", 90_000.00, "2025-01-08", "Department of Veterans Affairs", "621610")],
    ),
    "F": dict(
        name="EXAMPLE REGIONAL MEDICAL CENTER INC", uei="EXMPLF000006", line1="1 EXAMPLE HOSPITAL DRIVE", line2=None,
        officers=["Morgan T. Placeholder"], npi="A", official=("MORGAN", "PLACEHOLDER"),
        awards=[("contracts", "36C26323C0006", "CONT_AWD_36C26323C0006_3600", 5_200_000.00, "2023-11-01", "Department of Veterans Affairs", "622110")],
    ),
}

ENTITIES["G"] = dict(
    name="SUNRISE EXAMPLE LEARNING CENTER LLC", uei="EXMPLG000007", line1="300 EXAMPLE AVENUE", line2=None,
    officers=[], npi=None,
    awards=[("grants", "EXMPL-CACFP-0007", "ASST_NON_EXMPLCACFP0007_12D0", 1_200_000.00, "2024-10-01",
             "Department of Agriculture", "10.558")],
)
ENTITIES["H"] = dict(
    name="PINECREST EXAMPLE HOME CARE LLC", uei="EXMPLH000008", line1="77 NOWHERE EXAMPLE ROAD", line2=None,
    officers=[], npi=None,
    awards=[("contracts", "36C26325P0008", "CONT_AWD_36C26325P0008_3600", 260_000.00, "2025-05-19", "Department of Veterans Affairs", "621610")],
)
END_DATES = {"ASST_NON_EXMPLCACFP0007_12D0": "2025-09-30"}

FORMATION = {"EXMPLG000007": date(2024, 8, 1), "EXMPLA000001": date(2025, 1, 6), "EXMPLF000006": date(1987, 5, 1), "EXMPLC000003": date(2016, 8, 12)}

# Geocodes (fictional coordinates in a lake) and what OSM "shows" there.
GEO = {
    "4410 EXAMPLE LANE": (45.00010, -93.50010),
    "880 EXAMPLE PARKWAY": (45.01000, -93.52000),
    "880 EXAMPLE PKWY": (45.01000, -93.52000),
    "200 EXAMPLE BOULEVARD": (45.02000, -93.53000),
    "1 EXAMPLE HOSPITAL DRIVE": (45.03000, -93.54000),
    "300 EXAMPLE AVENUE": (45.04000, -93.55000),
}
OSM = {
    45.00010: [
        {"type": "way", "id": 900001, "center": {"lat": 45.00012, "lon": -93.50011}, "tags": {"building": "house"}},
        {"type": "way", "id": 900002, "center": {"lat": 45.00030, "lon": -93.50010}, "tags": {"building": "detached"}},
        {"type": "way", "id": 900003, "tags": {"landuse": "residential"}},
    ],
    45.01000: [
        {"type": "way", "id": 900010, "center": {"lat": 45.01002, "lon": -93.52001}, "tags": {"building": "retail"}},
        {"type": "node", "id": 900011, "lat": 45.01003, "lon": -93.52002,
         "tags": {"shop": "shipping", "name": "The UPS Store", "brand": "The UPS Store"}},
        {"type": "node", "id": 900012, "lat": 45.01005, "lon": -93.52004, "tags": {"shop": "dry_cleaning", "name": "Example Cleaners"}},
        {"type": "way", "id": 900013, "tags": {"landuse": "retail"}},
    ],
    45.02000: [
        {"type": "way", "id": 900020, "center": {"lat": 45.02001, "lon": -93.53001}, "tags": {"building": "office"}},
        {"type": "node", "id": 900021, "lat": 45.02002, "lon": -93.53002, "tags": {"office": "company", "name": "Example Office Park"}},
        {"type": "way", "id": 900022, "tags": {"landuse": "commercial"}},
    ],
    45.04000: [
        {"type": "way", "id": 900040, "center": {"lat": 45.04001, "lon": -93.55001}, "tags": {"building": "commercial"}},
        {"type": "node", "id": 900041, "lat": 45.04002, "lon": -93.55002, "tags": {"amenity": "kindergarten", "name": "Sunrise Example Learning Center"}},
        {"type": "way", "id": 900042, "tags": {"landuse": "commercial"}},
    ],
    45.03000: [
        {"type": "way", "id": 900030, "center": {"lat": 45.03001, "lon": -93.54001}, "tags": {"building": "hospital", "amenity": "hospital", "name": "Example Regional Medical Center"}},
        {"type": "way", "id": 900031, "tags": {"landuse": "institutional"}},
    ],
}


def _recipient_id(uei: str) -> str:
    return f"00000000-0000-0000-0000-{uei[-12:].lower().replace('exmpl', '0000a')}-C"


def _loc(ent: dict) -> dict:
    return {"address_line1": ent["line1"], "address_line2": ent["line2"], "address_line3": None, "city_name": CITY,
            "county_name": "EXAMPLE", "state_code": STATE, "zip": ZIP, "zip4": None, "country_code": "USA",
            "country_name": "UNITED STATES", "congressional_code": "99"}


def _award_rows(group: str) -> list[dict]:
    rows = []
    for ent in ENTITIES.values():
        for g, aid, gid, amt, signed, agency, code in ent["awards"]:
            if g != group:
                continue
            row = {"internal_id": zlib.crc32(gid.encode()) % 10**8, "Award ID": aid, "Recipient Name": ent["name"],
                   "Recipient UEI": ent["uei"], "recipient_id": _recipient_id(ent["uei"]), "Award Amount": amt,
                   "Start Date": signed, "End Date": END_DATES.get(gid, "2026-09-30"), "Awarding Agency": agency,
                   "Awarding Sub Agency": agency, "Description": "SYNTHETIC DEMO AWARD", "generated_internal_id": gid,
                   "awarding_agency_id": 1, "agency_slug": "example",
                   "Recipient Location": {"location_country_code": "USA", "country_name": "UNITED STATES", "state_code": STATE,
                                          "state_name": "Minnesota", "city_name": CITY, "county_code": "999", "county_name": "EXAMPLE",
                                          "address_line1": ent["line1"], "address_line2": ent["line2"], "address_line3": None,
                                          "congressional_code": "99", "zip4": None, "zip5": ZIP, "foreign_postal_code": None,
                                          "foreign_province": None}}
            if group == "contracts":
                row.update({"Contract Award Type": "PURCHASE ORDER", "NAICS": {"code": code, "description": "SYNTHETIC"},
                            "PSC": {"code": "Q201", "description": "MEDICAL- GENERAL HEALTH CARE"}})
            else:
                row["Award Type"] = "PROJECT GRANT (B)"
            rows.append(row)
    return sorted(rows, key=lambda r: -r["Award Amount"])


def _find_by(pred) -> dict | None:
    return next((e for e in ENTITIES.values() if pred(e)), None)


def _award_detail(gid: str) -> dict | None:
    for ent in ENTITIES.values():
        for g, aid, agid, amt, signed, _agency, code in ent["awards"]:
            if agid != gid:
                continue
            detail = {
                "id": 1, "generated_unique_award_id": gid, "piid" if g == "contracts" else "fain": aid,
                "category": "contract" if g == "contracts" else "grant", "total_obligation": amt, "date_signed": signed,
                "description": "SYNTHETIC DEMO AWARD",
                "period_of_performance": {"start_date": signed, "end_date": END_DATES.get(gid, "2026-09-30"), "last_modified_date": signed},
                "recipient": {"recipient_hash": _recipient_id(ent["uei"])[:-2], "recipient_name": ent["name"],
                              "recipient_uei": ent["uei"], "parent_recipient_uei": None, "parent_recipient_name": None,
                              "business_categories": ["Small Business", "Limited Liability Corporation"] if "LLC" in ent["name"] else ["Small Business"],
                              "location": {**_loc(ent), "zip5": ZIP}},
                "executive_details": {"officers": [{"name": n, "amount": 150000} for n in ent["officers"]]
                                      + [{"name": None, "amount": None}] * (5 - len(ent["officers"]))},
            }
            if g == "contracts":
                detail["latest_transaction_contract_data"] = {"naics": code, "product_or_service_code": "Q201"}
            else:
                detail["cfda_info"] = [{"cfda_number": code, "cfda_title": "SYNTHETIC PROGRAM"}]
            return detail
    return None


# Organizations that NPPES knows only by a different registered name, reachable through the ZIP sweep.
EXTRA_NPPES_ORGS: list[dict] = []


def _npi_results(org_query: str) -> list[dict]:
    from scrimmy.normalize import normalize_entity_name

    q = org_query.rstrip("*").upper()
    out = []
    for i, ent in enumerate(ENTITIES.values()):
        if not ent["npi"] or not normalize_entity_name(ent["name"]).startswith(q):
            continue
        out.append({
            "created_epoch": "1600000000000", "enumeration_type": "NPI-2", "last_updated_epoch": "1700000000000",
            "number": f"99999999{i:02d}",
            "addresses": [
                {"country_code": "US", "address_purpose": "LOCATION", "address_type": "DOM", "address_1": ent["line1"],
                 "address_2": ent["line2"] or "", "city": CITY, "state": STATE, "postal_code": f"{ZIP}0000"},
                {"country_code": "US", "address_purpose": "MAILING", "address_type": "DOM", "address_1": ent["line1"],
                 "address_2": ent["line2"] or "", "city": CITY, "state": STATE, "postal_code": f"{ZIP}0000"},
            ],
            "practiceLocations": [],
            "basic": {"organization_name": ent["name"], "organizational_subpart": "NO", "enumeration_date": "2016-09-01",
                      "last_updated": "2024-01-15", "status": ent["npi"],
                      "authorized_official_first_name": ent["official"][0], "authorized_official_last_name": ent["official"][1],
                      "authorized_official_title_or_position": "ADMINISTRATOR"},
            "taxonomies": [{"code": "251E00000X", "taxonomy_group": "", "desc": "Home Health", "state": STATE, "license": None, "primary": True}],
            "identifiers": [], "endpoints": [], "other_names": [],
        })
    return out


# A fictional state licensing dataset served as if from a Socrata portal.
SOCRATA_DOMAIN, SOCRATA_DATASET = "data.example.gov", "exmp-l1c3"
SOCRATA_COLUMNS = ["operation_id", "operation_name", "operation_type", "address_line", "city", "zip_code",
                   "total_capacity", "operation_status"]
LICENSES = [
    {"operation_id": "EX-100701", "operation_name": "SUNRISE EXAMPLE LEARNING CENTER", "operation_type": "Licensed Child Care Center",
     "address_line": "300 EXAMPLE AVE", "city": CITY, "zip_code": ZIP, "total_capacity": "40", "operation_status": "ACTIVE"},
    {"operation_id": "EX-100702", "operation_name": "LITTLE EXAMPLE FAMILY CHILD CARE", "operation_type": "Licensed Family Child Care Home",
     "address_line": "12 EXAMPLE CT", "city": CITY, "zip_code": ZIP, "total_capacity": "12", "operation_status": "ACTIVE"},
]

# A fictional LEIE extract in the published column layout.
LEIE_CSV = (
    "LASTNAME,FIRSTNAME,MIDNAME,BUSNAME,GENERAL,SPECIALTY,UPIN,NPI,DOB,ADDRESS,CITY,STATE,ZIP,EXCLTYPE,EXCLDATE,REINDATE,WAIVERDATE,WVRSTATE\n"
    f"PLACEHOLDER,RILEY,,,IND- LIC HC SERV PRO,NURSE/NURSES AIDE,,0000000000,19700101,9 EXAMPLE ST,{CITY},{STATE},{ZIP},1128b4,20210315,00000000,00000000,\n"
    f"EXAMPLEMAN,OTHER,Q,,IND- LIC HC SERV PRO,NURSE/NURSES AIDE,,0000000000,19700101,1 ELSEWHERE,SOMEWHERE,TX,75001,1128a1,20200101,00000000,00000000,\n"
).encode()


def _socrata(request: httpx.Request) -> httpx.Response:
    path = request.url.path
    if path == f"/api/views/{SOCRATA_DATASET}.json":
        return httpx.Response(200, json={"id": SOCRATA_DATASET, "name": "Example Licensed Child Care Operations",
                                         "columns": [{"fieldName": c, "name": c.replace("_", " ").title()} for c in SOCRATA_COLUMNS]})
    if path == f"/resource/{SOCRATA_DATASET}.json":
        where = request.url.params.get("$where", "")
        rows = [r for r in LICENSES if f"'{r['zip_code']}'" in where]
        if "like '%" in where:
            token = where.split("like '%")[1].split("%'")[0]
            rows = [r for r in rows if token in r["operation_name"].upper()]
        return httpx.Response(200, json=rows)
    return httpx.Response(404, json={"error": "not found"})


def _handler(request: httpx.Request) -> httpx.Response:
    url = request.url
    host, path = url.host, url.path
    if host == "api.usaspending.gov":
        if path.endswith("/search/spending_by_award/"):
            body = json.loads(request.content)
            codes = body["filters"]["award_type_codes"]
            group = "contracts" if "A" in codes else "grants" if "02" in codes else None
            rows = _award_rows(group) if group else []
            return httpx.Response(200, json={"limit": body["limit"], "results": rows,
                                             "page_metadata": {"page": body["page"], "hasNext": False}, "messages": []})
        if path.startswith("/api/v2/recipient/"):
            rid = path.rstrip("/").split("/")[-1]
            ent = _find_by(lambda e: _recipient_id(e["uei"]) == rid)
            if not ent:
                return httpx.Response(404, json={"detail": "Recipient not found"})
            return httpx.Response(200, json={
                "name": ent["name"], "alternate_names": [], "duns": None, "uei": ent["uei"], "recipient_id": rid,
                "recipient_level": "C", "parent_id": None, "parent_name": None, "parent_duns": None, "parent_uei": None,
                "parents": [], "business_types": ["category_business", "small_business"], "location": _loc(ent),
                "total_transaction_amount": sum(a[3] for a in ent["awards"]), "total_transactions": len(ent["awards"]),
            })
        if path.startswith("/api/v2/awards/"):
            detail = _award_detail(path.rstrip("/").split("/")[-1])
            return httpx.Response(200, json=detail) if detail else httpx.Response(404, json={"detail": "not found"})
    if host == SOCRATA_DOMAIN:
        return _socrata(request)
    if host == "npiregistry.cms.hhs.gov":
        params = url.params
        if params.get("organization_name"):
            results = _npi_results(params["organization_name"])
        elif params.get("postal_code"):  # ZIP sweep: every Type-2 org whose address starts with the 5-digit ZIP
            everyone = _npi_results("")
            zipped = [r for r in everyone if any(a["postal_code"].startswith(params["postal_code"]) for a in r["addresses"])]
            skip, limit = int(params.get("skip", 0)), int(params.get("limit", 10))
            results = (zipped + EXTRA_NPPES_ORGS)[skip:skip + limit]
        else:
            return httpx.Response(200, json={"Errors": [{"description": "No valid search criteria provided", "field": "generic", "number": "04"}]})
        return httpx.Response(200, json={"result_count": len(results), "results": results})
    if host == "geocoding.geo.census.gov":
        assert path == "/geocoder/geographies/onelineaddress", path
        address = url.params.get("address", "").upper()
        street = address.split(",")[0].strip()
        coords = GEO.get(street)
        matches = []
        if coords:
            matches = [{"tigerLine": {"side": "L", "tigerLineId": "99999" + str(zlib.crc32(street.encode()) % 10000)},
                        "coordinates": {"x": coords[1], "y": coords[0]},
                        "addressComponents": {"zip": ZIP, "streetName": "EXAMPLE", "city": CITY, "state": STATE},
                        "matchedAddress": f"{street}, {CITY}, {STATE}, {ZIP}",
                        "geographies": {
                            "States": [{"GEOID": "27", "NAME": "Minnesota", "STUSAB": "MN"}],
                            "Counties": [{"GEOID": "27999", "NAME": "Example County", "STATE": "27", "COUNTY": "999"}],
                            "Census Tracts": [{"GEOID": "27999000100", "TRACT": "000100", "NAME": "Census Tract 1"}],
                        }}]
        return httpx.Response(200, json={"result": {"input": {"address": {"address": address}, "benchmark": {"benchmarkName": "Public_AR_Current"}},
                                                    "addressMatches": matches}})
    if host == "overpass-api.de":
        query = parse_qs(request.content.decode())["data"][0]
        lat = float(query.split("around:")[1].split(",")[1])
        elements = next((v for k, v in OSM.items() if abs(k - lat) < 1e-6), [])
        return httpx.Response(200, json={"version": 0.6, "generator": "Overpass API (demo)", "osm3s": {"copyright": "ODbL"}, "elements": elements})
    return httpx.Response(404, json={"error": f"demo transport has no fixture for {url}"})


def fixture_transport() -> httpx.MockTransport:
    return httpx.MockTransport(_handler)


def fixture_formation_index() -> OperatorFormationIndex:
    idx = OperatorFormationIndex(path="demo")
    for uei, d in FORMATION.items():
        idx.by_uei[uei] = FormationDateRecord(d, "SYNTHETIC demo record standing in for a Secretary of State filing")
    return idx


def fixture_license_sources() -> list[LicenseSource]:
    return [LicenseSource(key="example_state_child_care", kind="socrata", states=[STATE], domain=SOCRATA_DOMAIN,
                          dataset_id=SOCRATA_DATASET, note="SYNTHETIC demo dataset")]


def fixture_settings(output_dir: Path, threshold: int = 50, profile: str = "registry") -> PipelineSettings:
    vertical = VERTICALS["home_health"]
    return PipelineSettings(
        filters=AwardSearchFilters(state=STATE, naics_codes=vertical.naics_codes, program_numbers=vertical.program_numbers,
                                   min_award=50_000, start_date=date(2023, 10, 1), end_date=date(2026, 9, 30)),
        award_groups=["contracts", "grants"],
        max_awards=100,
        threshold=threshold,
        output_dir=output_dir,
        vertical=vertical,
        enrichment=EnrichmentConfig(),
        formation_index=fixture_formation_index(),
        leie=LEIEMatcher.from_bytes(LEIE_CSV),
        license_sources=fixture_license_sources(),
        scoring=ScoringConfig.for_profile(profile, high_priority_threshold=threshold),
    )
