<!-- SAMPLE OUTPUT. Generated verbatim by `python -m scrimmy --demo --scoring-profile capacity`. Every entity, person, identifier and address in this file is FICTIONAL (synthetic fixtures in scrimmy/demo.py). -->

> **Sample output.** Produced unedited by `python -m scrimmy --demo --scoring-profile capacity` from synthetic fixtures. All names, people, UEIs and addresses are fictional.

# Investigative Lead Briefing: SUNRISE EXAMPLE LEARNING CENTER LLC

> **LEAD FOR HUMAN REVIEW. NOT A FINDING OF WRONGDOING.** Generated automatically from public records; no report has been submitted to any agency.

| Field | Value |
|---|---|
| Dossier ID | `SCRIMMY-20261021-FDDE4B4CFE9B` |
| Generated (UTC) | 2026-10-21 04:57:53 |
| Classification | **HIGH-PRIORITY FORENSIC LEAD**: score **55 / 100** (raw 55 of 170; profile `capacity`; threshold 50) |
| Suggested routing | A: criminal referral |
| Scan scope | state=MN; vertical=home_health; min award=$50,000.00; period=2023-10-01 to 2026-09-30 |
| Evidence directory | `dossiers/evidence/SCRIMMY-20261021-FDDE4B4CFE9B` (verify with `sha256sum -c SHA256SUMS`) |
| Narrative author | template |

## 1. Executive Summary & Lead Classification

This briefing summarizes an automated, rule-based screen of public records concerning SUNRISE EXAMPLE LEARNING CENTER LLC (UEI EXMPLG000007), address of record 300 EXAMPLE AVENUE, LAKEVIEW FALLS, MN 55990. Within the scan scope the recipient holds 1 federal award totaling $1,200,000.00 from Department of Agriculture. The deterministic screen assigned an anomaly score of 55/100 from 2 triggered indicators: Statistically Anomalous Disbursement-to-Capacity Ratio (+40); Newly Formed Windfall (+15). Benchmark arithmetic implies daily beneficiary volume of 14.286x the licensed capacity of 40. Suggested routing for counsel's consideration: Track A (see table below).

Each indicator describes an unverified discrepancy between public data sources (for example, a statistically anomalous disbursement-to-presence ratio or unresolved corporate co-location) and does not establish wrongdoing by any person or entity. Lawful explanations, including stale registry data, legitimate home-based or shared-office operations, sponsor or pass-through funding, and undisclosed affiliations, must be ruled out through the field checklist before any referral is considered. The following indicators could not be evaluated for lack of data and contributed no points: Serial Officer / Agent Registration.

| Track | Indicated | Basis |
|---|---|---|
| A: criminal referral (e.g. agency OIG / DOJ whistleblower program) | yes | entity-level registration, premises, capacity or exclusion indicators |
| B: civil False Claims Act / qui tam (via counsel) | no | multi-entity network, exclusion or reimbursement-volume indicators |

- Entity-level indicators (MATHEMATICAL_IMPOSSIBILITY, NEWLY_FORMED_WINDFALL) concern a single recipient's registration, premises or capacity, which suits a concentrated criminal referral (Track A).
- Routing is a suggestion for counsel. A qui tam complaint must be filed under seal by an attorney, and DOJ whistleblower programs have their own eligibility rules and reward terms (check justice.gov for the current terms).

## 2. Entity Identity & Federal Award Summary

| Attribute | Value |
|---|---|
| Legal name (USASpending) | SUNRISE EXAMPLE LEARNING CENTER LLC |
| UEI / DUNS / CAGE | EXMPLG000007 / n/a / n/a |
| Organizational NPI | not checked |
| Parent | n/a (n/a) |
| Address of record | 300 EXAMPLE AVENUE, LAKEVIEW FALLS, MN 55990 |
| Normalized location key | `300 EXAMPLE AVE|55990` |
| Business types | Limited Liability Corporation, Small Business, category_business, small_business |
| Formation date | 2024-08-01 (operator_csv: SYNTHETIC demo record standing in for a Secretary of State filing) |
| Healthcare classification | no |
| Officers / officials / agents | none published |
| Total awarded (in scope) | **$1,200,000.00** across 1 award(s) |

| Award ID | Type | Agency | Amount | Signed / Start | End | NAICS / PSC / Listing |
|---|---|---|---:|---|---|---|
| EXMPL-CACFP-0007 | PROJECT GRANT (B) | Department of Agriculture | $1,200,000.00 | 2024-10-01 | 2025-09-30 | 10.558 |

## 3. Flagged Discrepancies & Heuristic Breakdown

Scorer version `2.0.0`. Formula: `score = min(100, 35*0 + 40*1 + 20*0 + 0*0 + 25*0 + 15*1 + 20*0 + 15*0 + 0*0) = min(100, 55) = 55  [profile: capacity]`

| Rule | Weight | Status | Points | Rationale |
|---|---:|---|---:|---|
| `LEIE_EXCLUSION_MATCH`: Match to HHS-OIG List of Excluded Individuals/Entities | 35 | not triggered | 0 | No LEIE match for 1 subject(s) screened against 2 active exclusions. |
| `MATHEMATICAL_IMPOSSIBILITY`: Statistically Anomalous Disbursement-to-Capacity Ratio | 40 | **TRIGGERED** | 40 | Federal disbursements of $1,200,000.00 imply ~571.4 beneficiaries per operating day at the benchmark rate, 14.29x the licensed capacity of 40 (threshold > 2.0x). Statistically anomalous disbursement-to-capacity ratio; assumes the award funded beneficiary-days at this licensed site only. |
| `PHANTOM_HEALTHCARE_PROVIDER`: Healthcare Recipient Without Active Organizational NPI | 20 | not applicable | 0 | Recipient not classified as a healthcare provider. |
| `HIGH_DOLLAR_RESIDENTIAL_ZONE`: High-Dollar Award at Residential / Mail-Drop Address | 0 | not triggered | 0 | Premises classified as 'commercial', which is not in the residential/mail-drop set. |
| `PHYSICAL_SITE_MISMATCH`: Unverified Operating Capacity at Observed Site | 25 | not applicable | 0 | Funded services are not delivered at the recipient's premises (e.g. home health), so site appearance is not probative. |
| `NEWLY_FORMED_WINDFALL`: Newly Formed Windfall | 15 | **TRIGGERED** | 15 | Award EXMPL-CACFP-0007 ($1,200,000.00) has an effective date 61 days after the recorded formation date 2024-08-01 (threshold: < 120 days). High-variance award-to-operating-history ratio. |
| `CO_LOCATED_SHELL_CLUSTER`: Unresolved Corporate Co-location | 20 | not triggered | 0 | 1 unrelated awardee group(s) at this exact address (threshold: 3). |
| `EXECUTIVE_SERIAL_REGISTRATION`: Serial Officer / Agent Registration | 15 | insufficient data | 0 | No officers, authorized officials or registered agents published for this recipient. |
| `UNRESOLVED_PHYSICAL_DISCREPANCY`: Unresolved Physical Discrepancy | 0 | not triggered | 0 | Census matched '300 EXAMPLE AVENUE, LAKEVIEW FALLS, MN, 55990'. |
| **Total** | 170 | | **55** → score **55** | |

## 4. Evidence Matrix

### `MATHEMATICAL_IMPOSSIBILITY`: Statistically Anomalous Disbursement-to-Capacity Ratio (+40)

| # | Observation (field) | Observed value | Source record | Endpoint | Retrieved (UTC) |
|---|---|---|---|---|---|
| 1 | license.capacity | {"facility": "SUNRISE EXAMPLE LEARNING CENTER", "license_number": "EX-100701", "capacity": 40, "license_type": "Licensed Child Care Center", "name_similarity": 100.0} | `SRC-00053-2c5cdb4c7d` | `GET https://data.example.gov/resource/exmp-l1c3.json?%24where=starts_with%28zip_code%2C+%2755990%27%29+AND+upper%2…` | 2026-10-21 04:57:52 |
| 2 | capacity.formula _(benchmark: CACFP child care center: breakfast + lunch + snack per child-day (approx. free-tier, contiguous U.S.); USDA FNS CACFP reimbursement rates (annual Federal Register notice). Approximate; confirm the current school-year rates.)_ | annualized = $1,200,000.00 / max(1, 0.997) = $1,200,000.00; est. daily beneficiaries = $1,200,000.00 / ($8.40 x 250) = 571.43; ratio = 571.43 / 40 = 14.286 (threshold > 2.0) | `n/a` | derived | n/a |

### `NEWLY_FORMED_WINDFALL`: Newly Formed Windfall (+15)

| # | Observation (field) | Observed value | Source record | Endpoint | Retrieved (UTC) |
|---|---|---|---|---|---|
| 1 | formation_date _(source: operator_csv: SYNTHETIC demo record standing in for a Secretary of State filing)_ | 2024-08-01 | `n/a` | derived | n/a |
| 2 | award.effective_date / amount | {"award_id": "EXMPL-CACFP-0007", "date": "2024-10-01", "amount": 1200000.0} | `SRC-00002-5558f56104` | `POST https://api.usaspending.gov/api/v2/search/spending_by_award/` | 2026-10-21 04:57:41 |
| 3 | award.effective_date / amount | {"award_id": "EXMPL-CACFP-0007", "date": "2024-10-01", "amount": 1200000.0} | `SRC-00015-759174b49e` | `GET https://api.usaspending.gov/api/v2/awards/ASST_NON_EXMPLCACFP0007_12D0/` | 2026-10-21 04:57:46 |

## 5. Disbursement-to-Capacity Arithmetic

Matched state license: **SUNRISE EXAMPLE LEARNING CENTER**, license EX-100701, type Licensed Child Care Center, status ACTIVE, capacity **40** (source `socrata:data.example.gov/exmp-l1c3`, name similarity 100, address match yes).

| Input | Value |
|---|---|
| Benchmark | CACFP child care center: breakfast + lunch + snack per child-day (approx. free-tier, contiguous U.S.) |
| Unit rate (per beneficiary-day) | $8.40 |
| Operating days / year | 250 |
| Award total (benchmark-mapped awards) | $1,200,000.00 |
| Period | 2024-10-01 to 2025-09-30 (0.997 yr; annualized over max(1, years)) |
| Annualized disbursement | $1,200,000.00 |
| Estimated daily beneficiaries | **571.43** |
| Licensed capacity | **40** |
| Ratio (threshold > 2.0) | **14.286x** |

`annualized = $1,200,000.00 / max(1, 0.997) = $1,200,000.00; est. daily beneficiaries = $1,200,000.00 / ($8.40 x 250) = 571.43; ratio = 571.43 / 40 = 14.286 (threshold > 2.0)`

_Rate source: USDA FNS CACFP reimbursement rates (annual Federal Register notice). Approximate; confirm the current school-year rates._

## 6. Physical Site Verification

- **Census Geocoder**: matched `300 EXAMPLE AVENUE, LAKEVIEW FALLS, MN, 55990` at (45.04, -93.55), TIGER line 999997165.
- **OpenStreetMap land use**: `commercial` (nearest building tag `commercial`).
  - Nearest mapped structure is tagged building=commercial.
  - Commercial POIs within 40m: amenity=kindergarten (Sunrise Example Learning Center) ~3m.
- **Street-level vision**: not available (not run).

## 7. Exclusion Screening (HHS-OIG LEIE)

Screened 1 subject(s) against 2 active exclusions (file `in-memory`, SHA-256 `96e7b44804e7f0da…`).

_No matches._

## 8. Network Summary

Connected component: **1** awardee(s), $1,200,000.00 total awarded in scope. Graphviz DOT of the component is in the JSON dossier (`network.dot`).


## 9. Relevant Statutory Frameworks

_Frameworks a reviewing authority may consider if, and only if, independent verification substantiates the observations. Listing a statute is not a legal conclusion. Consult qualified counsel._

- **False Claims Act**, 31 U.S.C. §§ 3729-3733: Civil liability for knowingly presenting false claims for federal funds. Qui tam actions are filed under seal by counsel (§ 3730(b)). Relator share under § 3730(d): generally 15-25% if the government intervenes, 25-30% if it declines, and up to 10% where the action rests primarily on publicly disclosed information. § 3730(h) protects against retaliation.
- **False Claims Act: public disclosure bar**, 31 U.S.C. § 3730(e)(4): Qui tam actions based substantially on publicly disclosed information (including federal reports and databases such as USASpending, NPPES and the LEIE) may be dismissed unless the relator is an 'original source' whose independent knowledge materially adds to the public disclosures. Analysis of public data alone may not qualify; consult counsel before filing.
- **Wire fraud**, 18 U.S.C. § 1343: Use of interstate wire communications (including electronic claims, payment and grant-drawdown systems) in a scheme to obtain money by false pretenses.
- **Mail fraud**, 18 U.S.C. § 1341: Use of the mails in a scheme to obtain money by false pretenses.
- **False statements**, 18 U.S.C. § 1001: Materially false statements in matters within federal jurisdiction (e.g. SAM.gov registrations, grant applications, attendance or meal-count certifications).
- **Theft concerning programs receiving federal funds**, 18 U.S.C. § 666: Applies to organizations receiving more than $10,000 in federal program benefits in a one-year period.

## 10. Ground-Truthing Field Checklist

_Follow `docs/FIELD_INVESTIGATOR_SOP.md`. Public records and public right-of-way observation only._

- [ ] Confirm the licensed capacity, ages served and operating schedule directly from the state licensing record, and check whether the recipient operates other licensed sites that the award also funds.
- [ ] Replace the benchmark unit rate with the program's actual rate for the award period (CACFP: annual USDA notice; hospice: CMS final rule; CCDF: state payment rates) and recompute.
- [ ] Obtain claimed meal/attendance/patient-day counts through official records requests (FOIA; state public-records law) and compare them to capacity x operating days.
- [ ] Confirm the formation date from the state filing (not an aggregator); check for predecessor entities, mergers or name changes.
- [ ] Compare the award solicitation/notice date with the formation date and determine whether past performance or experience was certified.
- [ ] Re-run every source URL in the Source Ledger and confirm the responses still match the recorded SHA-256 hashes, or note what changed (run `sha256sum -c SHA256SUMS` in the evidence directory first).
- [ ] Pull the full award files (USASpending award pages, agency grant notices, Federal Audit Clearinghouse Single Audits) and record the stated place of performance, purpose and sub-recipients.
- [ ] Retrieve the Secretary of State filing history (formation date, registered agent, officers, status, annual reports) and save dated copies.
- [ ] Retrieve the SAM.gov entity registration (status, entity start date, physical vs. mailing address, POCs) and check SAM.gov exclusions.
- [ ] County deed / assessor audit: owner of record, property class code (residential/commercial), homestead exemption, transfer history, and whether the owner is connected to listed officers.
- [ ] State licensing: license type (family/home vs. center), licensed capacity, ages served, inspection and violation history at the address of record.
- [ ] Document every lawful explanation considered and why it was or was not ruled out.

## 11. Final Pre-Filing Review

_A short final check before anything is filed. It assumes section 10 is complete; it does not replace it._

- [ ] Every section 10 item is completed and documented, and each indicator is marked Explained, Unresolved or Corroborated.
- [ ] Each statement in the draft tip cites a primary record you retrieved yourself, not only this dossier.
- [ ] Observed facts are kept separate from inferences; the wording is neutral; no conclusions of guilt.
- [ ] Counsel has been consulted on routing (criminal tip vs. qui tam), the public disclosure bar, and original-source status.
- [ ] Nothing has been shared publicly or with the subject; the evidence folder hashes still verify.

## 12. Caveats & Limitations

- Scores reflect only the documented heuristics applied to public data at the time of retrieval. A high score is a prioritisation signal, not evidence of intent or wrongdoing.
- Cross-entity rules (co-location, serial registration) only consider recipients returned by this scan's filters.
- OpenStreetMap is volunteer-maintained; missing or outdated building/POI tags are common.
- Census Geocoder coordinates are interpolated along street segments and can be offset by tens of meters from the parcel.
- USASpending recipient addresses reflect the most recent registration data and may differ from the address at the time of award.
- Person-name matches use normalized names only and do not confirm that two records refer to the same individual.
- Home health agencies deliver care in clients' homes and often keep small administrative offices, so premises appearance is weak evidence; site visits should target the business office of record.
- Capacity arithmetic assumes the entire award paid for beneficiary-days at the matched licensed site at the benchmark rate. Sponsors, multi-site operators, administrative/capital funding and unspent obligations all break that assumption.

## 13. Chain of Custody

Raw artifacts archived write-once under `dossiers/evidence/SCRIMMY-20261021-FDDE4B4CFE9B`. `SHA256SUMS` there covers every file, including this dossier.

| Artifact | Kind | Bytes | SHA-256 | Source URL | Retrieved (UTC) |
|---|---|---:|---|---|---|
| `api/SRC-00002-5558f56104.json` | api_response | 2,312 | `919cda165bc3f9ff…` | https://api.usaspending.gov/api/v2/search/spending_by_award/ | 2026-10-21 04:57:41 |
| `api/SRC-00014-7b5daa0f7e.json` | api_response | 647 | `84c0c0e222055ca0…` | https://api.usaspending.gov/api/v2/recipient/00000000-0000-0000-0000-0000ag000007-C/?year= | 2026-10-21 04:57:45 |
| `api/SRC-00015-759174b49e.json` | api_response | 1,130 | `a0fbbc1df67e4cbb…` | https://api.usaspending.gov/api/v2/awards/ASST_NON_EXMPLCACFP0007_12D0/ | 2026-10-21 04:57:46 |
| `api/SRC-00050-c81396230a.json` | api_response | 384 | `2a6290521800c92a…` | https://geocoding.geo.census.gov/geocoder/locations/address?street=300+EXAMPLE+AVENUE&city | 2026-10-21 04:57:51 |
| `api/SRC-00051-7f73c1f882.json` | api_response | 386 | `f6285462bfcd3de7…` | https://overpass-api.de/api/interpreter | 2026-10-21 04:57:51 |
| `api/SRC-00052-919804ec34.json` | dataset_extract | 231 | `26b5da1fac6e6d31…` | https://oig.hhs.gov/exclusions/downloadables/UPDATED.csv | 2026-10-21 04:57:51 |
| `api/SRC-00053-2c5cdb4c7d.json` | api_response | 253 | `e133538425483b34…` | https://data.example.gov/resource/exmp-l1c3.json?%24where=starts_with%28zip_code%2C+%27559 | 2026-10-21 04:57:52 |
| `ledger.jsonl` | ledger | 9,248 | `9dd62fea2042109a…` |  |  |

| Record | Source | Request | HTTP | Retrieved (UTC) | Attempts | Response SHA-256 (prefix) |
|---|---|---|---|---|---:|---|
| `SRC-00002-5558f56104` | usaspending | `POST https://api.usaspending.gov/api/v2/search/spending_by_award/` | 200 | 2026-10-21 04:57:41 | 1 | `919cda165bc3f9ff` |
| `SRC-00014-7b5daa0f7e` | usaspending | `GET https://api.usaspending.gov/api/v2/recipient/00000000-0000-0000-0000-0000ag000007-C/?year=all` | 200 | 2026-10-21 04:57:45 | 1 | `84c0c0e222055ca0` |
| `SRC-00015-759174b49e` | usaspending | `GET https://api.usaspending.gov/api/v2/awards/ASST_NON_EXMPLCACFP0007_12D0/` | 200 | 2026-10-21 04:57:46 | 1 | `a0fbbc1df67e4cbb` |
| `SRC-00050-c81396230a` | census_geocoder | `GET https://geocoding.geo.census.gov/geocoder/locations/address?street=300+EXAMPLE+AVENUE&city=LAKEVIEW+FALLS&state=MN&zip=55990&benchmark=Public_AR_Current&format=json` | 200 | 2026-10-21 04:57:51 | 1 | `2a6290521800c92a` |
| `SRC-00051-7f73c1f882` | osm_overpass | `POST https://overpass-api.de/api/interpreter` | 200 | 2026-10-21 04:57:51 | 1 | `f6285462bfcd3de7` |
| `SRC-00052-919804ec34` | hhs_oig_leie | `FILE https://oig.hhs.gov/exclusions/downloadables/UPDATED.csv` |  | 2026-10-21 04:57:51 | 1 | `96e7b44804e7f0da` |
| `SRC-00053-2c5cdb4c7d` | socrata | `GET https://data.example.gov/resource/exmp-l1c3.json?%24where=starts_with%28zip_code%2C+%2755990%27%29+AND+upper%28operation_name%29+like+%27%25SUNRISE%25%27&%24limit=50` | 200 | 2026-10-21 04:57:52 | 1 | `e133538425483b34` |

---

_This dossier is an automated investigative LEAD generated from public records for review by a qualified human investigator and counsel. It is not a finding, allegation or accusation of wrongdoing by any person or entity. Every indicator describes an unverified discrepancy in public data that may have a lawful explanation (data-entry error, stale registry records, legitimate home-based operations, shared professional office space, sponsor or pass-through funding, affiliated entities not linked in public data, etc.). No report has been submitted to any agency by this tool. Do not publish, circulate or act on this document without independent verification._
