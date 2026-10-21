<!-- SAMPLE OUTPUT. Generated verbatim by `python -m scrimmy --demo`. Every entity, person, identifier and address in this file is FICTIONAL (synthetic fixtures in scrimmy/demo.py). -->

> **Sample output.** Produced unedited by `python -m scrimmy --demo` from synthetic fixtures. All names, people, UEIs and addresses are fictional.

# Investigative Lead Briefing: NORTHFIELD EXAMPLE HOME HEALTH LLC

> **LEAD FOR HUMAN REVIEW. NOT A FINDING OF WRONGDOING.** Generated automatically from public records; no report has been submitted to any agency.

| Field | Value |
|---|---|
| Dossier ID | `SCRIMMY-20261021-9988B9C5E845` |
| Generated (UTC) | 2026-10-21 04:24:16 |
| Anomaly score | **95 / 100** (raw total 95 of 115 possible; review threshold 50) |
| Scan scope | state=MN; vertical=home_health; min award=$50,000.00; period=2023-10-01 to 2026-09-30 |
| Narrative author | template |

## 1. Executive Summary

This briefing summarizes an automated, rule-based screen of public records concerning NORTHFIELD EXAMPLE HOME HEALTH LLC (UEI EXMPLA000001), address of record 4410 EXAMPLE LANE, LAKEVIEW FALLS, MN 55990. Within the scan scope the recipient holds 2 federal awards totaling $1,550,000.00 from Department of Health and Human Services, Department of Veterans Affairs. The deterministic screen assigned an anomaly score of 95/100 from 4 triggered indicators: Newly Formed Windfall (+30); High-Dollar Residential / Mail-Drop Address Mismatch (+25); Healthcare Recipient Without Active Organizational NPI (+25); Officer Shared Across Disconnected Awardees (+15).

Each indicator describes an unverified discrepancy between public data sources (for example, a high-variance award-to-presence ratio or an anomalous co-location pattern) and does not establish wrongdoing by any person or entity. Lawful explanations, including stale registry data, legitimate home-based or shared-office operations, and undisclosed affiliations, must be ruled out through the ground-truthing checklist before any referral is considered.

## 2. Entity Profile

| Attribute | Value |
|---|---|
| Legal name (USASpending) | NORTHFIELD EXAMPLE HOME HEALTH LLC |
| UEI / DUNS / CAGE | EXMPLA000001 / n/a / n/a |
| Parent | n/a (n/a) |
| Address of record | 4410 EXAMPLE LANE, LAKEVIEW FALLS, MN 55990 |
| Normalized location key | `4410 EXAMPLE LN|55990` |
| Business types | Limited Liability Corporation, Small Business, category_business, small_business |
| Formation date | 2025-01-06 (operator_csv: SYNTHETIC demo record standing in for a Secretary of State filing) |
| Healthcare classification | yes: scan vertical 'home_health'; award NAICS 621610 |
| Officers / officials | Jordan A. Sample (Highly compensated officer) |
| Total awarded (in scope) | **$1,550,000.00** across 2 award(s) |

| Award ID | Type | Agency | Amount | Signed / Start | NAICS / PSC / Listing |
|---|---|---|---:|---|---|
| 36C26325P0001 | PURCHASE ORDER | Department of Veterans Affairs | $1,240,000.00 | 2025-03-03 | 621610 / Q201 |
| EXMPL-GRANT-0001 | PROJECT GRANT (B) | Department of Health and Human Services | $310,000.00 | 2025-04-21 | 93.791 |

## 3. Deterministic Score Breakdown

Scorer version `1.0.0`. Formula: `score = min(100, 30*1 + 25*1 + 25*1 + 20*0 + 15*1) = min(100, 95) = 95`

| Rule | Weight | Status | Points | Rationale |
|---|---:|---|---:|---|
| R1_NEWLY_FORMED_WINDFALL: Newly Formed Windfall | 30 | **TRIGGERED** | 30 | Award 36C26325P0001 ($1,240,000.00) has an effective date 56 days after the recorded formation date 2025-01-06 (threshold: < 120 days). High-variance award-to-operating-history ratio. |
| R2_HIGH_DOLLAR_RESIDENTIAL_MISMATCH: High-Dollar Residential / Mail-Drop Address Mismatch | 25 | **TRIGGERED** | 25 | $1,550,000.00 awarded to an address of record that resolves to a mapped residential structure. Unverified physical discrepancy between award scale and observable business premises. |
| R3_PHANTOM_HEALTH_PROVIDER: Healthcare Recipient Without Active Organizational NPI | 25 | **TRIGGERED** | 25 | Healthcare-classified recipient (scan vertical 'home_health'; award NAICS 621610) has no active Type-2 (organizational) NPI in NPPES matching its name in MN after 2 query variant(s) and 0 candidate record(s). Unverified provider-registration discrepancy. |
| R4_MULTIPLE_SHELL_COLOCATION: Anomalous Multi-Entity Co-location | 20 | not triggered | 0 | 1 unrelated awardee group(s) at this exact address (threshold: 3). |
| R5_EXECUTIVE_DUPLICATION: Officer Shared Across Disconnected Awardees | 15 | **TRIGGERED** | 15 | 'Jordan A. Sample' also listed on 1 other entity with no shared UEI/parent or similar name, in the same locality (LAKEVIEW FALLS, MN). Anomalous officer-overlap pattern. |
| **Total** | 115 | | **95** → score **95** | |

## 4. Evidence Matrix

### R1_NEWLY_FORMED_WINDFALL: Newly Formed Windfall (+30)

| # | Observation (field) | Observed value | Source record | Endpoint | Retrieved (UTC) |
|---|---|---|---|---|---|
| 1 | formation_date _(source: operator_csv: SYNTHETIC demo record standing in for a Secretary of State filing)_ | 2025-01-06 | `n/a` | derived | n/a |
| 2 | award.effective_date / amount | {"award_id": "36C26325P0001", "date": "2025-03-03", "amount": 1240000.0} | `SRC-00001-1a1c09e7a3` | `POST https://api.usaspending.gov/api/v2/search/spending_by_award/` | 2026-10-21 04:24:07 |
| 3 | award.effective_date / amount | {"award_id": "36C26325P0001", "date": "2025-03-03", "amount": 1240000.0} | `SRC-00006-98c58fe514` | `GET https://api.usaspending.gov/api/v2/awards/CONT_AWD_36C26325P0001_3600/` | 2026-10-21 04:24:09 |

### R2_HIGH_DOLLAR_RESIDENTIAL_MISMATCH: High-Dollar Residential / Mail-Drop Address Mismatch (+25)

| # | Observation (field) | Observed value | Source record | Endpoint | Retrieved (UTC) |
|---|---|---|---|---|---|
| 1 | award.amount | {"award_id": "36C26325P0001", "amount": 1240000.0} | `SRC-00001-1a1c09e7a3` | `POST https://api.usaspending.gov/api/v2/search/spending_by_award/` | 2026-10-21 04:24:07 |
| 2 | award.amount | {"award_id": "EXMPL-GRANT-0001", "amount": 310000.0} | `SRC-00002-5558f56104` | `POST https://api.usaspending.gov/api/v2/search/spending_by_award/` | 2026-10-21 04:24:07 |
| 3 | sum(award.amount) _(derived: 2 award(s) in scope)_ | 1550000.0 | `n/a` | derived | n/a |
| 4 | census.addressMatches[0] | {"input": "4410 EXAMPLE LANE, LAKEVIEW FALLS, MN 55990", "matched": "4410 EXAMPLE LANE, LAKEVIEW FALLS, MN, 55990", "lat": 45.0001, "lon": -93.5001} | `SRC-00019-6bb37001b5` | `GET https://geocoding.geo.census.gov/geocoder/locations/address?street=4410+EXAMPLE+LANE&city=LAKEVIEW+FALLS&state…` | 2026-10-21 04:24:12 |
| 5 | osm.classification | {"classification": "residential", "signals": ["Nearest mapped structure is tagged building=house (residential).", "No commercial, office or service POIs mapped within 40m."], "nearest_building": "house", "landuse": ["residential"]} | `SRC-00020-e278d3d52c` | `POST https://overpass-api.de/api/interpreter` | 2026-10-21 04:24:13 |

### R3_PHANTOM_HEALTH_PROVIDER: Healthcare Recipient Without Active Organizational NPI (+25)

| # | Observation (field) | Observed value | Source record | Endpoint | Retrieved (UTC) |
|---|---|---|---|---|---|
| 1 | nppes.results _(healthcare basis: scan vertical 'home_health'; award NAICS 621610)_ | {"queries": ["NORTHFIELD EXAMPLE HOME HEALTH*", "NORTHFIELD EXAMPLE*"], "state": "MN", "candidates_examined": 0, "accepted_matches": []} | `SRC-00021-cfcec24422` | `GET https://npiregistry.cms.hhs.gov/api/?version=2.1&enumeration_type=NPI-2&organization_name=NORTHFIELD+EXAMPLE+H…` | 2026-10-21 04:24:13 |
| 2 | nppes.results _(healthcare basis: scan vertical 'home_health'; award NAICS 621610)_ | {"queries": ["NORTHFIELD EXAMPLE HOME HEALTH*", "NORTHFIELD EXAMPLE*"], "state": "MN", "candidates_examined": 0, "accepted_matches": []} | `SRC-00022-ea7a787235` | `GET https://npiregistry.cms.hhs.gov/api/?version=2.1&enumeration_type=NPI-2&organization_name=NORTHFIELD+EXAMPLE%2…` | 2026-10-21 04:24:14 |

### R5_EXECUTIVE_DUPLICATION: Officer Shared Across Disconnected Awardees (+15)

| # | Observation (field) | Observed value | Source record | Endpoint | Retrieved (UTC) |
|---|---|---|---|---|---|
| 1 | NORTHFIELD EXAMPLE HOME HEALTH LLC.officers (usaspending.executive_details) | Jordan A. Sample | `SRC-00006-98c58fe514` | `GET https://api.usaspending.gov/api/v2/awards/CONT_AWD_36C26325P0001_3600/` | 2026-10-21 04:24:09 |
| 2 | LAKEVIEW EXAMPLE PERSONAL CARE LLC.officers _(Name-only match; identity of the individual is not confirmed.)_ | SAMPLE, JORDAN | `SRC-00015-4495301c36` | `GET https://api.usaspending.gov/api/v2/awards/ASST_NON_EXMPLGRANT0003_7530/` | 2026-10-21 04:24:12 |

## 5. Specific Inconsistencies

- **Newly Formed Windfall.** Award 36C26325P0001 ($1,240,000.00) has an effective date 56 days after the recorded formation date 2025-01-06 (threshold: < 120 days). High-variance award-to-operating-history ratio.
- **High-Dollar Residential / Mail-Drop Address Mismatch.** $1,550,000.00 awarded to an address of record that resolves to a mapped residential structure. Unverified physical discrepancy between award scale and observable business premises.
- **Healthcare Recipient Without Active Organizational NPI.** Healthcare-classified recipient (scan vertical 'home_health'; award NAICS 621610) has no active Type-2 (organizational) NPI in NPPES matching its name in MN after 2 query variant(s) and 0 candidate record(s). Unverified provider-registration discrepancy.
- **Officer Shared Across Disconnected Awardees.** 'Jordan A. Sample' also listed on 1 other entity with no shared UEI/parent or similar name, in the same locality (LAKEVIEW FALLS, MN). Anomalous officer-overlap pattern.
- _Premises signal (residential)_: Nearest mapped structure is tagged building=house (residential).
- _Premises signal (residential)_: No commercial, office or service POIs mapped within 40m.
- _Geocode_: Census matched `4410 EXAMPLE LANE, LAKEVIEW FALLS, MN, 55990` at (45.0001, -93.5001), TIGER line 999995040.
- _NPPES_: no accepted organizational NPI match for queries `NORTHFIELD EXAMPLE HOME HEALTH*`, `NORTHFIELD EXAMPLE*` (0 candidates examined).

## 6. Related Entities

| Entity | UEI | Address | Total awarded | Officers | Linked by |
|---|---|---|---:|---|---|
| LAKEVIEW EXAMPLE PERSONAL CARE LLC | EXMPLC000003 | 200 EXAMPLE BOULEVARD, SUITE 5, LAKEVIEW FALLS, MN 55990 | $180,000.00 | SAMPLE, JORDAN; RILEY PLACEHOLDER | R5_EXECUTIVE_DUPLICATION |

## 7. Statutory Cross-References

_Frameworks a reviewing authority may consider if, and only if, independent verification substantiates the observations. Listing a statute is not a legal conclusion. Consult qualified counsel._

- **False Claims Act**, 31 U.S.C. §§ 3729-3733: Civil liability for knowingly presenting false claims for federal funds; § 3730(b) qui tam provisions and § 3730(h) anti-retaliation protections for whistleblowers.
- **False Claims Act - public disclosure bar**, 31 U.S.C. § 3730(e)(4): Qui tam actions based on publicly disclosed information (including federal reports and databases such as USASpending) may be barred unless the relator is an 'original source' with independent knowledge. Consult counsel.
- **Wire fraud**, 18 U.S.C. § 1343: Federal offense involving interstate wire communications (including electronic payment and grant-drawdown systems) in a scheme to obtain money by false pretenses.
- **Mail fraud**, 18 U.S.C. § 1341: Federal offense involving use of the mails in a scheme to obtain money by false pretenses.
- **False statements**, 18 U.S.C. § 1001: Materially false statements in matters within federal jurisdiction (e.g. SAM.gov registrations, grant applications, certifications).
- **Theft or bribery concerning programs receiving federal funds**, 18 U.S.C. § 666: Applies to organizations receiving more than $10,000 in federal program benefits in a one-year period.
- **Health care fraud**, 18 U.S.C. § 1347: Schemes to defraud any health care benefit program, including Medicare and Medicaid.
- **Civil Monetary Penalties Law**, 42 U.S.C. § 1320a-7a: Administrative penalties for false or fraudulent claims to federal health care programs.
- **Conspiracy to defraud the United States**, 18 U.S.C. § 371: Relevant where multiple coordinated entities or individuals are involved.

## 8. Recommended Ground-Truthing Checklist

- [ ] Confirm the formation date directly from the state filing (not a third-party aggregator); check for predecessor entities, mergers or name changes that would explain a short operating history.
- [ ] Compare the award solicitation/notice date to the formation date; determine whether the entity certified prior experience or past performance in its application.
- [ ] From a public right-of-way only, photograph the premises with date/time and GPS metadata: signage, business hours, deliveries, client traffic. Do not trespass, enter, or contact occupants.
- [ ] Check Google Street View / county imagery history for the address to see whether a business presence existed during the award period.
- [ ] If the address is a CMRA/PMB or P.O. Box, determine the entity's actual place of performance from award documents, licensing records and NPPES practice locations.
- [ ] Check zoning and home-occupation permits with the municipality; confirm whether the activity funded is permitted at a residence.
- [ ] Search NPPES manually by name variants, DBA names, prior names and individual practitioners; check whether services were billed under an individual (Type 1) NPI or a parent organization.
- [ ] Check the state Medicaid provider enrollment directory and CMS Provider Enrollment (PECOS) data where publicly available.
- [ ] Confirm whether the funded activity actually required NPI enumeration (e.g. grant administration or research does not).
- [ ] Confirm the individual's identity across entities using full name plus middle name, title, and corporate filings; common names produce false matches.
- [ ] Determine whether the entities are lawfully affiliated (franchise, management company, common ownership disclosed in filings) and whether that relationship was disclosed in award applications.
- [ ] Re-run every source URL in the Source Ledger and confirm the responses still match the recorded SHA-256 hashes or note what changed.
- [ ] Pull the full award files (USASpending award pages, FPDS/FAC audits, agency grant notices) for each award listed and record the stated place of performance and purpose.
- [ ] Retrieve the entity's Secretary of State filing history (formation date, registered agent, officers, status, annual reports) and save dated copies.
- [ ] Retrieve the SAM.gov entity registration (status, entity start date, physical vs. mailing address, POCs, exclusions) and check SAM.gov exclusions and HHS-OIG LEIE.
- [ ] Search county property-tax / assessor records for the address of record: owner of record, property class code (residential/commercial), homestead exemption status.
- [ ] Check whether the entity holds the operating licenses its awards would require (state child care licensing, health facility licensing, professional licenses) and the licensed capacity at the address.
- [ ] Search the Federal Audit Clearinghouse for Single Audit reports if the entity expended $1,000,000 or more (FY2025+ threshold; $750,000 previously) in federal awards in a fiscal year.
- [ ] Document innocent explanations considered and why each was or was not ruled out.

## 9. Caveats & Limitations

- Scores reflect only the five documented heuristics applied to public data at the time of retrieval; a high score is a prioritisation signal, not evidence of intent or wrongdoing.
- Cross-entity rules (co-location, officer overlap) only consider recipients returned by this scan's filters; entities outside the scan are not compared.
- OpenStreetMap is volunteer-maintained; missing or outdated building/POI tags are common, especially in rural and newly built areas.
- Census Geocoder coordinates are interpolated along street segments and can be offset by tens of meters from the actual parcel.
- USASpending recipient addresses reflect the most recent SAM/award data and may not match the address at the time of award.
- Person-name matches are on normalized names only and do not confirm that two records refer to the same individual.
- Home-and-community-based service agencies often maintain small administrative offices; care is delivered in clients' homes, so site visits should target the business office of record.
- Residential classification derives from OSM building/land-use tags, not municipal zoning; verify with the local zoning map and assessor class code.

## 10. Source Ledger (Chain of Custody)

| Record | Source | Request | HTTP | Retrieved (UTC) | Attempts | SHA-256 (prefix) |
|---|---|---|---|---|---:|---|
| `SRC-00001-1a1c09e7a3` | usaspending | `POST https://api.usaspending.gov/api/v2/search/spending_by_award/` | 200 | 2026-10-21 04:24:07 | 1 | `68472465aed60cac` |
| `SRC-00002-5558f56104` | usaspending | `POST https://api.usaspending.gov/api/v2/search/spending_by_award/` | 200 | 2026-10-21 04:24:07 | 1 | `71403aaab5f253b9` |
| `SRC-00005-090d1f627a` | usaspending | `GET https://api.usaspending.gov/api/v2/recipient/00000000-0000-0000-0000-0000aa000001-C/?year=all` | 200 | 2026-10-21 04:24:08 | 1 | `366cfe51a55924b9` |
| `SRC-00006-98c58fe514` | usaspending | `GET https://api.usaspending.gov/api/v2/awards/CONT_AWD_36C26325P0001_3600/` | 200 | 2026-10-21 04:24:09 | 1 | `a2fc00568aace718` |
| `SRC-00007-fd7e2ad97d` | usaspending | `GET https://api.usaspending.gov/api/v2/awards/ASST_NON_EXMPLGRANT0001_7530/` | 200 | 2026-10-21 04:24:09 | 1 | `241abe7e9426dc94` |
| `SRC-00015-4495301c36` | usaspending | `GET https://api.usaspending.gov/api/v2/awards/ASST_NON_EXMPLGRANT0003_7530/` | 200 | 2026-10-21 04:24:12 | 1 | `efb1737a42c88306` |
| `SRC-00019-6bb37001b5` | census_geocoder | `GET https://geocoding.geo.census.gov/geocoder/locations/address?street=4410+EXAMPLE+LANE&city=LAKEVIEW+FALLS&state=MN&zip=55990&benchmark=Public_AR_Current&format=json` | 200 | 2026-10-21 04:24:12 | 1 | `16f3d3cd6aa4eb3a` |
| `SRC-00020-e278d3d52c` | osm_overpass | `POST https://overpass-api.de/api/interpreter` | 200 | 2026-10-21 04:24:13 | 1 | `94dccf6116455446` |
| `SRC-00021-cfcec24422` | nppes | `GET https://npiregistry.cms.hhs.gov/api/?version=2.1&enumeration_type=NPI-2&organization_name=NORTHFIELD+EXAMPLE+HOME+HEALTH%2A&state=MN&limit=200` | 200 | 2026-10-21 04:24:13 | 1 | `d1d2693d18cee969` |
| `SRC-00022-ea7a787235` | nppes | `GET https://npiregistry.cms.hhs.gov/api/?version=2.1&enumeration_type=NPI-2&organization_name=NORTHFIELD+EXAMPLE%2A&state=MN&limit=200` | 200 | 2026-10-21 04:24:14 | 1 | `d1d2693d18cee969` |

Full raw response snapshots and request bodies are embedded in the companion JSON dossier (`sources[].raw_snippet`).

---

_This dossier is an automated investigative LEAD generated from public records for review by a qualified human investigator. It is not a finding, allegation or accusation of wrongdoing by any person or entity. Every indicator below describes an unverified discrepancy in public data that may have a lawful explanation (data-entry error, stale registry records, legitimate home-based operations, shared professional office space, affiliated entities not linked in public data, etc.). No report has been submitted to any agency by this tool. Do not publish, circulate or act on this document without independent verification._
