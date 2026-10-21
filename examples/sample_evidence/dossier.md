# Investigative Lead Briefing: NORTHFIELD EXAMPLE HOME HEALTH LLC

> **LEAD FOR HUMAN REVIEW. NOT A FINDING OF WRONGDOING.** Generated automatically from public records; no report has been submitted to any agency.

| Field | Value |
|---|---|
| Dossier ID | `SCRIMMY-20261021-843F0DE06EFC` |
| Generated (UTC) | 2026-10-21 13:18:12 |
| Classification | **HIGH-PRIORITY FORENSIC LEAD**: score **90 / 100** (raw 90 of 200; profile `registry`; threshold 50) |
| Suggested routing | B: civil FCA / qui tam (via counsel) |
| Scan scope | state=MN; vertical=home_health; min award=$50,000.00; period=2023-10-01 to 2026-09-30 |
| Evidence directory | `dossiers_registry/evidence/SCRIMMY-20261021-843F0DE06EFC` (verify with `sha256sum -c SHA256SUMS`) |
| Narrative author | template |

## 1. Executive Summary & Lead Classification

This briefing summarizes an automated, rule-based screen of public records concerning NORTHFIELD EXAMPLE HOME HEALTH LLC (UEI EXMPLA000001), address of record 4410 EXAMPLE LANE, LAKEVIEW FALLS, MN 55990. Within the scan scope the recipient holds 2 federal awards totaling $1,550,000.00 from Department of Health and Human Services, Department of Veterans Affairs. The deterministic screen assigned an anomaly score of 90/100 from 4 triggered indicators: Healthcare Recipient Without Active Organizational NPI (+30); High-Dollar Award at Residential / Mail-Drop Address (+25); Newly Formed Windfall (+20); Serial Officer / Agent Registration (+15). Suggested routing for counsel's consideration: Track B (see table below).

Each indicator describes an unverified discrepancy between public data sources (for example, a statistically anomalous disbursement-to-presence ratio or unresolved corporate co-location) and does not establish wrongdoing by any person or entity. Lawful explanations, including stale registry data, legitimate home-based or shared-office operations, sponsor or pass-through funding, and undisclosed affiliations, must be ruled out through the field checklist before any referral is considered. The following indicators could not be evaluated for lack of data and contributed no points: Statistically Anomalous Disbursement-to-Capacity Ratio.

| Track | Indicated | Basis |
|---|---|---|
| A: criminal referral (e.g. agency OIG / DOJ whistleblower program) | yes | entity-level registration, premises, capacity or exclusion indicators |
| B: civil False Claims Act / qui tam (via counsel) | yes | multi-entity network, exclusion or reimbursement-volume indicators |

- Entity-level indicators (HIGH_DOLLAR_RESIDENTIAL_ZONE, NEWLY_FORMED_WINDFALL, PHANTOM_HEALTHCARE_PROVIDER) concern a single recipient's registration, premises or capacity, which suits a concentrated criminal referral (Track A).
- Network indicators (EXECUTIVE_SERIAL_REGISTRATION) link this recipient to other awardees, the multi-entity pattern counsel evaluates for a civil False Claims Act / qui tam action (Track B).
- Routing is a suggestion for counsel. A qui tam complaint must be filed under seal by an attorney, and DOJ whistleblower programs have their own eligibility rules and reward terms (check justice.gov for the current terms).

## 2. Strike Force Targeting

_No Strike Force preset was selected for this scan._

## 3. Entity Identity & Federal Award Summary

| Attribute | Value |
|---|---|
| Legal name (USASpending) | NORTHFIELD EXAMPLE HOME HEALTH LLC |
| UEI / DUNS / CAGE | EXMPLA000001 / n/a / n/a |
| Organizational NPI | none matched |
| Parent | n/a (n/a) |
| Address of record | 4410 EXAMPLE LANE, LAKEVIEW FALLS, MN 55990 |
| Normalized location key | `4410 EXAMPLE LN|55990` |
| Business types | Limited Liability Corporation, Small Business, category_business, small_business |
| Formation date | 2025-01-06 (operator_csv: SYNTHETIC demo record standing in for a Secretary of State filing) |
| Healthcare classification | yes: award NAICS 621610; Assistance Listing 93.791 in healthcare vertical 'home_health' |
| Officers / officials / agents | Jordan A. Sample (Highly compensated officer) |
| Total awarded (in scope) | **$1,550,000.00** across 2 award(s) |

| Award ID | Type | Agency | Amount | Signed / Start | End | NAICS / PSC / Listing |
|---|---|---|---:|---|---|---|
| 36C26325P0001 | PURCHASE ORDER | Department of Veterans Affairs | $1,240,000.00 | 2025-03-03 | 2026-09-30 | 621610 / Q201 |
| EXMPL-GRANT-0001 | PROJECT GRANT (B) | Department of Health and Human Services | $310,000.00 | 2025-04-21 | 2026-09-30 | 93.791 |

## 4. Flagged Discrepancies & Heuristic Breakdown

Scorer version `2.0.0`. Formula: `score = min(100, 40*0 + 40*0 + 30*1 + 25*1 + 0*0 + 20*1 + 20*0 + 15*1 + 10*0) = min(100, 90) = 90  [profile: registry]`

| Rule | Weight | Status | Points | Rationale |
|---|---:|---|---:|---|
| `LEIE_EXCLUSION_MATCH`: Match to HHS-OIG List of Excluded Individuals/Entities | 40 | not triggered | 0 | No LEIE match for 2 subject(s) screened against 2 active exclusions. |
| `MATHEMATICAL_IMPOSSIBILITY`: Statistically Anomalous Disbursement-to-Capacity Ratio | 40 | insufficient data | 0 | No award maps to a per-beneficiary benchmark (program/NAICS/vertical). |
| `PHANTOM_HEALTHCARE_PROVIDER`: Healthcare Recipient Without Active Organizational NPI | 30 | **TRIGGERED** | 30 | Healthcare-classified recipient (award NAICS 621610; Assistance Listing 93.791 in healthcare vertical 'home_health') has no active Type-2 (organizational) NPI in NPPES matching its name in MN after 2 query variant(s) and 0 candidate record(s). Unverified provider-registration discrepancy. |
| `HIGH_DOLLAR_RESIDENTIAL_ZONE`: High-Dollar Award at Residential / Mail-Drop Address | 25 | **TRIGGERED** | 25 | $1,550,000.00 awarded to an address of record that resolves to a mapped residential structure. Statistically anomalous disbursement-to-presence ratio; unverified physical discrepancy. |
| `PHYSICAL_SITE_MISMATCH`: Unverified Operating Capacity at Observed Site | 0 | not applicable | 0 | Funded services are not delivered at the recipient's premises (e.g. home health), so site appearance is not probative. |
| `NEWLY_FORMED_WINDFALL`: Newly Formed Windfall | 20 | **TRIGGERED** | 20 | Award 36C26325P0001 ($1,240,000.00) has an effective date 56 days after the recorded formation date 2025-01-06 (threshold: < 120 days). High-variance award-to-operating-history ratio. |
| `CO_LOCATED_SHELL_CLUSTER`: Unresolved Corporate Co-location | 20 | not triggered | 0 | 1 unrelated awardee group(s) at this exact address (threshold: 3). |
| `EXECUTIVE_SERIAL_REGISTRATION`: Serial Officer / Agent Registration | 15 | **TRIGGERED** | 15 | 'Jordan A. Sample' is listed on 2 unrelated awardees in LAKEVIEW FALLS, MN (threshold: 2). Anomalous officer-overlap pattern. |
| `UNRESOLVED_PHYSICAL_DISCREPANCY`: Unresolved Physical Discrepancy | 10 | not triggered | 0 | Census matched '4410 EXAMPLE LANE, LAKEVIEW FALLS, MN, 55990'. |
| **Total** | 200 | | **90** → score **90** | |

## 5. Evidence Matrix

### `PHANTOM_HEALTHCARE_PROVIDER`: Healthcare Recipient Without Active Organizational NPI (+30)

| # | Observation (field) | Observed value | Source record | Endpoint | Retrieved (UTC) |
|---|---|---|---|---|---|
| 1 | nppes.results _(healthcare basis: award NAICS 621610; Assistance Listing 93.791 in healthcare vertical 'home_health')_ | {"queries": ["NORTHFIELD EXAMPLE HOME HEALTH*", "NORTHFIELD EXAMPLE*"], "state": "MN", "candidates_examined": 0, "accepted_matches": []} | `SRC-00029-cfcec24422` | `GET https://npiregistry.cms.hhs.gov/api/?version=2.1&enumeration_type=NPI-2&organization_name=NORTHFIELD+EXAMPLE+H…` | 2026-10-21 13:18:12 |
| 2 | nppes.results _(healthcare basis: award NAICS 621610; Assistance Listing 93.791 in healthcare vertical 'home_health')_ | {"queries": ["NORTHFIELD EXAMPLE HOME HEALTH*", "NORTHFIELD EXAMPLE*"], "state": "MN", "candidates_examined": 0, "accepted_matches": []} | `SRC-00030-ea7a787235` | `GET https://npiregistry.cms.hhs.gov/api/?version=2.1&enumeration_type=NPI-2&organization_name=NORTHFIELD+EXAMPLE%2…` | 2026-10-21 13:18:12 |

### `HIGH_DOLLAR_RESIDENTIAL_ZONE`: High-Dollar Award at Residential / Mail-Drop Address (+25)

| # | Observation (field) | Observed value | Source record | Endpoint | Retrieved (UTC) |
|---|---|---|---|---|---|
| 1 | award.amount | {"award_id": "36C26325P0001", "amount": 1240000.0} | `SRC-00001-d28314550a` | `POST https://api.usaspending.gov/api/v2/search/spending_by_award/` | 2026-10-21 13:18:11 |
| 2 | award.amount | {"award_id": "EXMPL-GRANT-0001", "amount": 310000.0} | `SRC-00002-5558f56104` | `POST https://api.usaspending.gov/api/v2/search/spending_by_award/` | 2026-10-21 13:18:11 |
| 3 | sum(award.amount) _(derived: 2 award(s) in scope)_ | 1550000.0 | `n/a` | derived | n/a |
| 4 | census.addressMatches[0] | {"input": "4410 EXAMPLE LANE, LAKEVIEW FALLS, MN 55990", "matched": "4410 EXAMPLE LANE, LAKEVIEW FALLS, MN, 55990", "lat": 45.0001, "lon": -93.5001} | `SRC-00027-7a8ff3c874` | `GET https://geocoding.geo.census.gov/geocoder/geographies/onelineaddress?address=4410+EXAMPLE+LANE%2C+LAKEVIEW+FAL…` | 2026-10-21 13:18:12 |
| 5 | osm.classification | {"classification": "residential", "signals": ["Nearest mapped structure is tagged building=house (residential).", "No commercial, office or service POIs mapped within 40m."], "nearest_building": "house", "landuse": ["residential"]} | `SRC-00028-e278d3d52c` | `POST https://overpass-api.de/api/interpreter` | 2026-10-21 13:18:12 |

### `NEWLY_FORMED_WINDFALL`: Newly Formed Windfall (+20)

| # | Observation (field) | Observed value | Source record | Endpoint | Retrieved (UTC) |
|---|---|---|---|---|---|
| 1 | formation_date _(source: operator_csv: SYNTHETIC demo record standing in for a Secretary of State filing)_ | 2025-01-06 | `n/a` | derived | n/a |
| 2 | award.effective_date / amount | {"award_id": "36C26325P0001", "date": "2025-03-03", "amount": 1240000.0} | `SRC-00001-d28314550a` | `POST https://api.usaspending.gov/api/v2/search/spending_by_award/` | 2026-10-21 13:18:11 |
| 3 | award.effective_date / amount | {"award_id": "36C26325P0001", "date": "2025-03-03", "amount": 1240000.0} | `SRC-00006-98c58fe514` | `GET https://api.usaspending.gov/api/v2/awards/CONT_AWD_36C26325P0001_3600/` | 2026-10-21 13:18:11 |

### `EXECUTIVE_SERIAL_REGISTRATION`: Serial Officer / Agent Registration (+15)

| # | Observation (field) | Observed value | Source record | Endpoint | Retrieved (UTC) |
|---|---|---|---|---|---|
| 1 | NORTHFIELD EXAMPLE HOME HEALTH LLC.officers | Jordan A. Sample | `SRC-00006-98c58fe514` | `GET https://api.usaspending.gov/api/v2/awards/CONT_AWD_36C26325P0001_3600/` | 2026-10-21 13:18:11 |
| 2 | LAKEVIEW EXAMPLE PERSONAL CARE LLC.officers _(Name-only match; identity of the individual is not confirmed.)_ | SAMPLE, JORDAN | `SRC-00019-4495301c36` | `GET https://api.usaspending.gov/api/v2/awards/ASST_NON_EXMPLGRANT0003_7530/` | 2026-10-21 13:18:11 |

## 6. Mathematical Discrepancy Analysis

No state license matched (2 candidates examined in socrata:data.example.gov/exmp-l1c3).

_Not computed: No award maps to a per-beneficiary benchmark (program/NAICS/vertical)._

## 7. Physical Site Verification

- **Census Geocoder**: matched `4410 EXAMPLE LANE, LAKEVIEW FALLS, MN, 55990` at (45.0001, -93.5001), TIGER line 999995040.
- **OpenStreetMap land use**: `residential` (nearest building tag `house`).
  - Nearest mapped structure is tagged building=house (residential).
  - No commercial, office or service POIs mapped within 40m.
- **Street-level vision**: not available (not run).

## 8. Exclusion Screening (HHS-OIG LEIE)

Screened 2 subject(s) against 2 active exclusions (file `in-memory`, SHA-256 `96e7b44804e7f0da…`).

_No matches._

## 9. Network Summary

Connected component: **2** awardee(s), $1,730,000.00 total awarded in scope. Graphviz DOT of the component is in the JSON dossier (`network.dot`).

- Shared person 'SAMPLE, JORDAN': NORTHFIELD EXAMPLE HOME HEALTH LLC; LAKEVIEW EXAMPLE PERSONAL CARE LLC

| Related entity | UEI | Address | Total awarded | People | Linked by |
|---|---|---|---:|---|---|
| LAKEVIEW EXAMPLE PERSONAL CARE LLC | EXMPLC000003 | 200 EXAMPLE BOULEVARD, SUITE 5, LAKEVIEW FALLS, MN 55990 | $180,000.00 | SAMPLE, JORDAN; RILEY PLACEHOLDER | EXECUTIVE_SERIAL_REGISTRATION |

## 10. Relevant Statutory Frameworks

_Frameworks a reviewing authority may consider if, and only if, independent verification substantiates the observations. Listing a statute is not a legal conclusion. Consult qualified counsel._

- **False Claims Act**, 31 U.S.C. §§ 3729-3733: Civil liability for knowingly presenting false claims for federal funds. Qui tam actions are filed under seal by counsel (§ 3730(b)). Relator share under § 3730(d): generally 15-25% if the government intervenes, 25-30% if it declines, and up to 10% where the action rests primarily on publicly disclosed information. § 3730(h) protects against retaliation.
- **False Claims Act: public disclosure bar**, 31 U.S.C. § 3730(e)(4): Qui tam actions based substantially on publicly disclosed information (including federal reports and databases such as USASpending, NPPES and the LEIE) may be dismissed unless the relator is an 'original source' whose independent knowledge materially adds to the public disclosures. Analysis of public data alone may not qualify; consult counsel before filing.
- **Wire fraud**, 18 U.S.C. § 1343: Use of interstate wire communications (including electronic claims, payment and grant-drawdown systems) in a scheme to obtain money by false pretenses.
- **Mail fraud**, 18 U.S.C. § 1341: Use of the mails in a scheme to obtain money by false pretenses.
- **Health care fraud**, 18 U.S.C. § 1347: Schemes to defraud any health care benefit program, including Medicare and Medicaid.
- **Civil Monetary Penalties Law**, 42 U.S.C. § 1320a-7a: Administrative penalties for false claims to federal health care programs, including claims for items or services furnished, ordered or directed by an excluded individual or entity.
- **False statements**, 18 U.S.C. § 1001: Materially false statements in matters within federal jurisdiction (e.g. SAM.gov registrations, grant applications, attendance or meal-count certifications).
- **Theft concerning programs receiving federal funds**, 18 U.S.C. § 666: Applies to organizations receiving more than $10,000 in federal program benefits in a one-year period.
- **Conspiracy to defraud the United States**, 18 U.S.C. § 371: Relevant where multiple coordinated entities or individuals are involved.

## 11. 2-Minute Physical Verification Checklist

_Fast triage, using public sources, of whether this lead deserves the full field work in section 12. It does not replace section 12._

- [ ] Street View / aerial imagery at the geocoded point: is there a building with signage consistent with the funded service (child care, home health office, hospice)?
- [ ] State license lookup for the entity name: is there an active license at this address, and do the license type and capacity match section 6?
- [ ] NPPES search (healthcare recipients): is there an active Type-2 NPI whose practice location is this address?
- [ ] Secretary of State business search: is the entity active, and do the formation date and officers match this dossier?
- [ ] HHS-OIG LEIE online search for each listed officer or authorized official.
- [ ] If any check above resolves an indicator, mark it Explained and stop; otherwise continue with the full field checklist (section 12).

## 12. Ground-Truthing Field Checklist

_Follow `docs/FIELD_INVESTIGATOR_SOP.md`. Public records and public right-of-way observation only._

- [ ] Search NPPES manually by name variants, DBA names, prior names and individual practitioners; check whether services were billed under an individual (Type 1) NPI or a parent organization.
- [ ] Check the state Medicaid provider enrollment directory, and CMS Provider Enrollment (PECOS) data where public.
- [ ] Confirm whether the funded activity actually required NPI enumeration (grant administration and research do not).
- [ ] From a public right-of-way only, photograph the premises with date/time/GPS metadata: signage, posted hours, deliveries. Do not trespass, enter, contact occupants, or photograph people.
- [ ] Check Street View / county imagery history for a business presence during the award period.
- [ ] If the address is a CMRA/PMB or P.O. Box, establish the actual place of performance from award documents, licensing records and NPPES practice locations.
- [ ] Check zoning and home-occupation permits with the municipality.
- [ ] Confirm the formation date from the state filing (not an aggregator); check for predecessor entities, mergers or name changes.
- [ ] Compare the award solicitation/notice date with the formation date and determine whether past performance or experience was certified.
- [ ] Confirm the individual's identity across entities (full name, middle name, title, filings); common names produce false matches.
- [ ] Determine whether the entities are lawfully affiliated (franchise, management company, disclosed common ownership).
- [ ] Re-run every source URL in the Source Ledger and confirm the responses still match the recorded SHA-256 hashes, or note what changed (run `sha256sum -c SHA256SUMS` in the evidence directory first).
- [ ] Pull the full award files (USASpending award pages, agency grant notices, Federal Audit Clearinghouse Single Audits) and record the stated place of performance, purpose and sub-recipients.
- [ ] Retrieve the Secretary of State filing history (formation date, registered agent, officers, status, annual reports) and save dated copies.
- [ ] Retrieve the SAM.gov entity registration (status, entity start date, physical vs. mailing address, POCs) and check SAM.gov exclusions.
- [ ] County deed / assessor audit: owner of record, property class code (residential/commercial), homestead exemption, transfer history, and whether the owner is connected to listed officers.
- [ ] State licensing: license type (family/home vs. center), licensed capacity, ages served, inspection and violation history at the address of record.
- [ ] Document every lawful explanation considered and why it was or was not ruled out.

## 13. Final Pre-Filing Review

_A short final check before anything is filed. It assumes section 12 is complete; it does not replace it._

- [ ] Every section 12 item is completed and documented, and each indicator is marked Explained, Unresolved or Corroborated.
- [ ] Each statement in the draft tip cites a primary record you retrieved yourself, not only this dossier.
- [ ] Observed facts are kept separate from inferences; the wording is neutral; no conclusions of guilt.
- [ ] Counsel has been consulted on routing (criminal tip vs. qui tam), the public disclosure bar, and original-source status.
- [ ] Nothing has been shared publicly or with the subject; the evidence folder hashes still verify.

## 14. Caveats & Limitations

- Scores reflect only the documented heuristics applied to public data at the time of retrieval. A high score is a prioritisation signal, not evidence of intent or wrongdoing.
- Cross-entity rules (co-location, serial registration) only consider recipients returned by this scan's filters.
- OpenStreetMap is volunteer-maintained; missing or outdated building/POI tags are common.
- Census Geocoder coordinates are interpolated along street segments and can be offset by tens of meters from the parcel.
- USASpending recipient addresses reflect the most recent registration data and may differ from the address at the time of award.
- Person-name matches use normalized names only and do not confirm that two records refer to the same individual.
- Home health agencies deliver care in clients' homes and often keep small administrative offices, so premises appearance is weak evidence; site visits should target the business office of record.
- Residential classification derives from OSM building/land-use tags, not municipal zoning; verify with the zoning map and assessor class code.

## 15. Chain of Custody

Raw API responses are archived write-once under `dossiers_registry/evidence/SCRIMMY-20261021-843F0DE06EFC`. `provenance.json` there records, for every call, the exact URL, request body, UTC timestamp, HTTP status and the SHA-256 of the response as received. `SHA256SUMS` covers every file, including this dossier.

| Artifact | Kind | Bytes | SHA-256 | Source URL | Retrieved (UTC) |
|---|---|---:|---|---|---|
| `api/SRC-00001-d28314550a.json` | api_response | 3,421 | `16a59f72424265f9…` | https://api.usaspending.gov/api/v2/search/spending_by_award/ | 2026-10-21 13:18:11 |
| `api/SRC-00002-5558f56104.json` | api_response | 2,312 | `919cda165bc3f9ff…` | https://api.usaspending.gov/api/v2/search/spending_by_award/ | 2026-10-21 13:18:11 |
| `api/SRC-00005-090d1f627a.json` | api_response | 645 | `366cfe51a55924b9…` | https://api.usaspending.gov/api/v2/recipient/00000000-0000-0000-0000-0000aa000001-C/?year= | 2026-10-21 13:18:11 |
| `api/SRC-00006-98c58fe514.json` | api_response | 1,158 | `a2fc00568aace718…` | https://api.usaspending.gov/api/v2/awards/CONT_AWD_36C26325P0001_3600/ | 2026-10-21 13:18:11 |
| `api/SRC-00007-fd7e2ad97d.json` | api_response | 1,143 | `241abe7e9426dc94…` | https://api.usaspending.gov/api/v2/awards/ASST_NON_EXMPLGRANT0001_7530/ | 2026-10-21 13:18:11 |
| `api/SRC-00019-4495301c36.json` | api_response | 1,150 | `efb1737a42c88306…` | https://api.usaspending.gov/api/v2/awards/ASST_NON_EXMPLGRANT0003_7530/ | 2026-10-21 13:18:11 |
| `api/SRC-00027-7a8ff3c874.json` | api_response | 654 | `9160407cdd9a6560…` | https://geocoding.geo.census.gov/geocoder/geographies/onelineaddress?address=4410+EXAMPLE+ | 2026-10-21 13:18:12 |
| `api/SRC-00028-e278d3d52c.json` | api_response | 346 | `94dccf6116455446…` | https://overpass-api.de/api/interpreter | 2026-10-21 13:18:12 |
| `api/SRC-00029-cfcec24422.json` | api_response | 31 | `d1d2693d18cee969…` | https://npiregistry.cms.hhs.gov/api/?version=2.1&enumeration_type=NPI-2&organization_name= | 2026-10-21 13:18:12 |
| `api/SRC-00030-ea7a787235.json` | api_response | 31 | `d1d2693d18cee969…` | https://npiregistry.cms.hhs.gov/api/?version=2.1&enumeration_type=NPI-2&organization_name= | 2026-10-21 13:18:12 |
| `api/SRC-00031-da2745ce0f.json` | dataset_extract | 254 | `d07fd7b7ec1b6ae6…` | https://oig.hhs.gov/exclusions/downloadables/UPDATED.csv | 2026-10-21 13:18:12 |
| `api/SRC-00032-4b96cad94d.json` | api_response | 2 | `4f53cda18c2baa0c…` | https://data.example.gov/resource/exmp-l1c3.json?%24where=starts_with%28zip_code%2C+%27559 | 2026-10-21 13:18:12 |
| `api/SRC-00033-0013bad5c8.json` | api_response | 509 | `9ce383e9052786e7…` | https://data.example.gov/resource/exmp-l1c3.json?%24where=starts_with%28zip_code%2C+%27559 | 2026-10-21 13:18:12 |
| `ledger.jsonl` | ledger | 18,691 | `f5570637d805d730…` |  |  |

| Record | Source | Request | HTTP | Retrieved (UTC) | Attempts | Response SHA-256 (prefix) |
|---|---|---|---|---|---:|---|
| `SRC-00001-d28314550a` | usaspending | `POST https://api.usaspending.gov/api/v2/search/spending_by_award/` | 200 | 2026-10-21 13:18:11 | 1 | `16a59f72424265f9` |
| `SRC-00002-5558f56104` | usaspending | `POST https://api.usaspending.gov/api/v2/search/spending_by_award/` | 200 | 2026-10-21 13:18:11 | 1 | `919cda165bc3f9ff` |
| `SRC-00005-090d1f627a` | usaspending | `GET https://api.usaspending.gov/api/v2/recipient/00000000-0000-0000-0000-0000aa000001-C/?year=all` | 200 | 2026-10-21 13:18:11 | 1 | `366cfe51a55924b9` |
| `SRC-00006-98c58fe514` | usaspending | `GET https://api.usaspending.gov/api/v2/awards/CONT_AWD_36C26325P0001_3600/` | 200 | 2026-10-21 13:18:11 | 1 | `a2fc00568aace718` |
| `SRC-00007-fd7e2ad97d` | usaspending | `GET https://api.usaspending.gov/api/v2/awards/ASST_NON_EXMPLGRANT0001_7530/` | 200 | 2026-10-21 13:18:11 | 1 | `241abe7e9426dc94` |
| `SRC-00019-4495301c36` | usaspending | `GET https://api.usaspending.gov/api/v2/awards/ASST_NON_EXMPLGRANT0003_7530/` | 200 | 2026-10-21 13:18:11 | 1 | `efb1737a42c88306` |
| `SRC-00027-7a8ff3c874` | census_geocoder | `GET https://geocoding.geo.census.gov/geocoder/geographies/onelineaddress?address=4410+EXAMPLE+LANE%2C+LAKEVIEW+FALLS%2C+MN+55990&benchmark=Public_AR_Current&vintage=Current_Current&format=json` | 200 | 2026-10-21 13:18:12 | 1 | `9160407cdd9a6560` |
| `SRC-00028-e278d3d52c` | osm_overpass | `POST https://overpass-api.de/api/interpreter` | 200 | 2026-10-21 13:18:12 | 1 | `94dccf6116455446` |
| `SRC-00029-cfcec24422` | nppes | `GET https://npiregistry.cms.hhs.gov/api/?version=2.1&enumeration_type=NPI-2&organization_name=NORTHFIELD+EXAMPLE+HOME+HEALTH%2A&state=MN&limit=200` | 200 | 2026-10-21 13:18:12 | 1 | `d1d2693d18cee969` |
| `SRC-00030-ea7a787235` | nppes | `GET https://npiregistry.cms.hhs.gov/api/?version=2.1&enumeration_type=NPI-2&organization_name=NORTHFIELD+EXAMPLE%2A&state=MN&limit=200` | 200 | 2026-10-21 13:18:12 | 1 | `d1d2693d18cee969` |
| `SRC-00031-da2745ce0f` | hhs_oig_leie | `FILE https://oig.hhs.gov/exclusions/downloadables/UPDATED.csv` |  | 2026-10-21 13:18:12 | 1 | `96e7b44804e7f0da` |
| `SRC-00032-4b96cad94d` | socrata | `GET https://data.example.gov/resource/exmp-l1c3.json?%24where=starts_with%28zip_code%2C+%2755990%27%29+AND+upper%28operation_name%29+like+%27%25NORTHFIELD%25%27&%24limit=50` | 200 | 2026-10-21 13:18:12 | 1 | `4f53cda18c2baa0c` |
| `SRC-00033-0013bad5c8` | socrata | `GET https://data.example.gov/resource/exmp-l1c3.json?%24where=starts_with%28zip_code%2C+%2755990%27%29&%24limit=500` | 200 | 2026-10-21 13:18:12 | 1 | `9ce383e9052786e7` |

---

_This dossier is an automated investigative LEAD generated from public records for review by a qualified human investigator and counsel. It is not a finding, allegation or accusation of wrongdoing by any person or entity. Every indicator describes an unverified discrepancy in public data that may have a lawful explanation (data-entry error, stale registry records, legitimate home-based operations, shared professional office space, sponsor or pass-through funding, affiliated entities not linked in public data, etc.). No report has been submitted to any agency by this tool. Do not publish, circulate or act on this document without independent verification._
