# Scrimmy Fraud Detector

Scrimmy is an open-source, deterministic **lead-generation and evidence-compilation** pipeline. It screens recipients of federal awards, grants and subsidies for documented anomaly patterns, archives the raw public records behind every finding with SHA-256 integrity, and writes dossiers for **human** forensic review and whistleblower counsel.

> Scrimmy produces *leads*, not findings. It never submits reports, tips or complaints anywhere. Every output is an unverified starting point that a person must check with primary records under the [Field Investigator SOP](docs/FIELD_INVESTIGATOR_SOP.md), with counsel, before taking any action.

## Pipeline

```
 USASpending ─▶ Enrichment & state licensing ─▶ Capacity arithmetic ─▶ Network graph ─▶ Deterministic scorer
 awards         NPPES · Census · OSM Overpass    (math_engine)          (networkx)       (0-100, 9 rules)
                HHS-OIG LEIE · Socrata/CSV                                                    │
                licensing · SAM.gov (opt.)                                                    ▼
                                         Dossier (.json + .md) ◀─ Evidence vault ◀─ Vision site check (opt.,
                                         per lead                 /evidence/<id>/    near-threshold leads only)
       every HTTP exchange ─▶ EvidenceLedger (URL, request, UTC time, attempts, SHA-256, raw bytes)
```

| Module | Role |
|---|---|
| `scrimmy/models.py` | Strict Pydantic v2 schemas: entities, awards, enrichment, LEIE, licenses, capacity, vision, flags, dossiers, evidence |
| `scrimmy/clients.py` | `httpx` clients for USASpending, NPPES, Census, Overpass, Socrata, SAM.gov, Google Street View and Mapillary. Per-host throttling, exponential backoff with jitter, `Retry-After`, timeouts, evidence ledger |
| `scrimmy/ingestion.py` | Award search, entity resolution, recipient profiles, award details (officers, signature dates) |
| `scrimmy/enrichment.py` | Cross-referencer: NPI matching, geocoding, OSM land use, formation dates and filings, LEIE, licensing, capacity |
| `scrimmy/leie_matcher.py` | HHS-OIG LEIE CSV loader and matcher (NPI / strong / possible match strengths) |
| `scrimmy/licensing.py` | State licensing capacity from Socrata datasets or operator-exported CSVs, with column auto-detection |
| `scrimmy/math_engine.py` | Capacity-to-disbursement arithmetic with cited, overridable benchmark rates |
| `scrimmy/vision_verifier.py` | Street-level imagery retrieval and vision-model classification with strict JSON validation |
| `scrimmy/network_graph.py` | `networkx` officer/entity/address graph, affiliation grouping, co-location and serial-registration queries, DOT export |
| `scrimmy/scorer.py` | `DeterministicScorer` and the `registry` / `capacity` weight profiles |
| `scrimmy/evidence.py` | Write-once evidence vault, `provenance.json`, `SHA256SUMS`, verification |
| `scrimmy/dossier.py` | Evidence provenance & dossier exporter: one call writes the JSON dossier, the Markdown briefing and the hashed evidence archive |
| `scrimmy/synthesizer.py` | Track A/B routing, statutes, checklists, guarded LLM summary, Markdown/JSON rendering |
| `scrimmy/pipeline.py`, `scrimmy/options.py`, `scrimmy/main.py` | Orchestrator, shared option builder, CLI |
| `public/` + `api/index.py` + `scrimmy/service.py` | Hosted web console for Vercel: static front end plus a stateless FastAPI function |
| `streamlit_app/app.py` | Streamlit console for hosts that run a long-lived process |
| `tests/fixtures/api_fixtures.py` | Synthetic API responses for the **test suite only**. No runtime code imports them; every scan calls the live endpoints |

The briefs named some files differently; they map as follows: `ingestor.py` → `ingestion.py`, `enricher.py` → `enrichment.py`, `api_client.py` → `clients.py`, `forensic_engine.py` → `scorer.py` + `math_engine.py`, and `app.py` → `streamlit_app/app.py` (moved out of the repo root so Vercel cannot mistake it for the Python entrypoint).

## Install

```bash
python3.11 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt            # core pipeline, CLI and API
pip install -r requirements-llm.txt        # optional: anthropic / openai / litellm (summaries, vision)
pip install -r requirements-dev.txt        # optional: pytest
```

## Web console

There are two front ends over the same pipeline. Both make live calls only and contain no sample data. If a scan finds nothing above the threshold you get an empty state, and if the APIs are unreachable the console says so.

### Hosted on Vercel (`public/` + `api/index.py`)

Vercel runs serverless functions: each request must finish within the function's time limit, and functions cannot hold a WebSocket. So the hosted console splits a scan into short, stateless API steps (`search` → `enrich` one recipient at a time → `score` → optional `site-check` → `dossier` / `evidence`). The browser drives them and keeps the scan state, including the raw response bytes for the evidence archive.

1. Import the repo in Vercel. `vercel.json` sets the framework to **Other**, serves `public/`, and routes `/api/*` to the Python function. No build command is needed. Python is pinned by `.python-version`.
2. Optional environment variables (Project → Settings → Environment Variables):
   - `SCRIMMY_CONTACT`: your email, sent in the User-Agent as a courtesy to public API operators.
   - `SCRIMMY_ACCESS_TOKEN`: if set, every API call must send it. Set it unless you want anyone with the URL to run scans from your Vercel account.
   - `ANTHROPIC_API_KEY` / `OPENAI_API_KEY`: LLM-written summary prose.
   - `MAPILLARY_TOKEN` or `GOOGLE_MAPS_API_KEY`, plus an LLM key: street-level vision checks.
3. Open the deployment URL, pick verticals, state, award floor and fiscal year, then **Run live scan**. Each recipient is a separate function call, so a scan takes about as long as the CLI (a few seconds per recipient, two at a time).

Run the same layout locally with `python scripts/serve_local.py` (http://localhost:8000).

Hosted limits: licensing sources must be public Socrata datasets (pasted as JSON in the sidebar), since a serverless function has no local files. The LEIE file is downloaded into the function's `/tmp` and cached for up to 7 days per warm instance.

### Streamlit (`streamlit_app/app.py`)

```bash
pip install -r streamlit_app/requirements.txt
streamlit run streamlit_app/app.py            # http://localhost:8501
```

Streamlit keeps a WebSocket open to a long-running server, so it **cannot run on Vercel**. Use Streamlit Community Cloud (entrypoint `streamlit_app/app.py`; it reads `streamlit_app/requirements.txt`), Render, Railway, Fly.io or a VM. Its views are the same as the hosted console: **Strike Force Scanner** (summary metrics and lead cards that expand into an inline inspector with exports), **Lead Inspector** (identity, map, score breakdown, mathematical breakdown, raw payloads, network graph, checklists), **Anomaly Matrix** (every recipient × every rule) and **Dossier Export**. It can also download the LEIE and accept an uploaded licensing-sources JSON.

## CLI

```bash
# DOJ Health Care Fraud Strike Force presets (focus counties scanned by county FIPS):
python -m scrimmy --strike-force florida --fiscal-year 2025 --output ./dossiers
python -m scrimmy --strike-force gulf_coast --min-award 500000 --leie-csv ./data/LEIE_UPDATED.csv

# Custom states, or nationwide (bounded by --max-awards):
python -m scrimmy --state MN --state WI --fiscal-year 2025
python -m scrimmy --nationwide --max-awards 300 --min-award 1000000

# Default targeting (child day care & nutrition 624410/624210, home health 621610, hospice 623110/624120), $250k floor:
export SCRIMMY_CONTACT="you@example.org"     # courtesy contact sent in the User-Agent
python -m scrimmy leie-download --output ./data/LEIE_UPDATED.csv
python -m scrimmy --state MN --fiscal-year 2025 --leie-csv ./data/LEIE_UPDATED.csv --output ./dossiers

# Child care with capacity arithmetic from state licensing data:
python -m scrimmy --state TX --vertical childcare --scoring-profile capacity \
  --license-sources config/license_sources.example.json --unit-rate child_care_subsidy_day=55 --leie-csv ./data/LEIE_UPDATED.csv

# Add street-level site checks (needs an imagery key and a vision model):
MAPILLARY_TOKEN=... ANTHROPIC_API_KEY=... python -m scrimmy --state MN --vertical hospice --scoring-profile capacity --vision

# Verify an evidence archive later:
python -m scrimmy verify-evidence ./dossiers/evidence/SCRIMMY-20261021-XXXXXXXXXXXX
```

Run `python -m scrimmy --help` for every option.

### Outputs

- `<output>/SCRIMMY-<date>-<id>_<name>.{json,md}`: the dossier. The JSON holds the full entity, enrichment, rule evaluations with evidence, capacity formula, network DOT, track assessment and every cited `SourceRecord`. The Markdown is the briefing, for intake agents and whistleblower counsel, with sections covering the Executive Summary & Lead Classification, Strike Force Targeting (district, U.S. Attorney's Office, county), Entity Identity & Federal Award Summary (UEI, CAGE, NPI, award IDs), Flagged Discrepancies, Evidence Matrix, Mathematical Discrepancy Analysis, Physical Site Verification, LEIE Screening, Network Summary, Statutory Frameworks, a 2-Minute Physical Verification Checklist, the Ground-Truthing Field Checklist, Final Pre-Filing Review, Caveats, and Chain of Custody.
- `<output>/evidence/<lead_id>/`: write-once raw API responses (byte-for-byte), `ledger.jsonl`, `provenance.json` (per call: URL, request body, UTC timestamp, HTTP status, response SHA-256, archived file and its SHA-256), `dossier.json`, `dossier.md` and `SHA256SUMS` (check with `sha256sum -c SHA256SUMS`).
- `<output>/scan_manifest_<ts>.json` and `source_ledger_<ts>.jsonl`: every screened entity and every HTTP exchange in the scan.

Samples, generated unedited by the pipeline from the synthetic test fixtures (fictional data): [`examples/sample_dossier.md`](examples/sample_dossier.md) (registry profile), [`examples/sample_dossier_capacity.md`](examples/sample_dossier_capacity.md) (capacity profile), and [`examples/sample_evidence/`](examples/sample_evidence/) (a full evidence archive).

## Scoring (deterministic, no LLM)

`score = min(100, Σ weightᵢ × triggeredᵢ)`. Every rule reports `triggered`, `not_triggered`, `insufficient_data` or `not_applicable`, with a rationale and evidence. Missing data **never** adds points. Leads at or above `--threshold` (default 50) are marked **High-Priority Forensic Leads**.

| Rule ID | Shown as | registry | capacity | Triggers when |
|---|---|---:|---:|---|
| `LEIE_EXCLUSION_MATCH` | Match to HHS-OIG LEIE | 40 | 35 | The entity, an officer, an authorized official or a registered agent matches an active LEIE row by NPI, or by name + state + (city, ZIP or middle initial). Name + state alone is reported for review but never scored. |
| `MATHEMATICAL_IMPOSSIBILITY` | Statistically Anomalous Disbursement-to-Capacity Ratio | 40 | 40 | annualized award ÷ (unit rate × units per day × 365) ÷ capacity > 2.0, where capacity is the matched state license or, failing that, a facility-type ceiling |
| `PHANTOM_HEALTHCARE_PROVIDER` | Healthcare Recipient Without Active Organizational NPI | 30 | 20 | A healthcare-classified recipient has no active Type-2 NPI after **both** stages of `NPPESClient.get_npi()` finish: (1) cleaned name variants in its state (punctuation and entity suffixes stripped, ST/SAINT spellings), then (2) a sweep of every Type-2 NPI in its ZIP code, matched by street number (plus token-set name similarity ≥ 70) or fuzzy name (token-set ≥ 90). If the ZIP is unknown, the sweep fails, or it is incomplete, the rule reports insufficient data instead of +points |
| `HIGH_DOLLAR_RESIDENTIAL_ZONE` | High-Dollar Award at Residential / Mail-Drop Address | 25 | 0 | > $250k awarded at an OSM-residential structure, a CMRA/PMB, or a P.O. Box (not scored if the matched license is home-based) |
| `PHYSICAL_SITE_MISMATCH` | Unverified Operating Capacity at Observed Site | 0 | 25 | An on-site-service recipient with > $250k awarded, whose imagery (within 4 years of the award) is classified as a residence, mail drop or vacant lot at confidence ≥ 0.75, with no commercial presence mapped in OSM |
| `NEWLY_FORMED_WINDFALL` | Newly Formed Windfall | 20 | 15 | A major award (≥ $100k) dated less than 120 days after formation |
| `CO_LOCATED_SHELL_CLUSTER` | Unresolved Corporate Co-location | 20 | 20 | ≥ 3 unrelated awardees at the exact same street + unit + ZIP |
| `EXECUTIVE_SERIAL_REGISTRATION` | Serial Officer / Agent Registration | 15 | 15 | One person on ≥ 2 (registry) or ≥ 3 (capacity) unrelated awardees in the same city. Commercial registered-agent services are excluded |
| `UNRESOLVED_PHYSICAL_DISCREPANCY` | Unresolved Physical Discrepancy | 10 | 0 | The address of record does not match any Census TIGER address range |

The two weight tables in the briefs differed, so both are kept as profiles. `registry` needs no keys. `capacity` uses licensing data and, optionally, vision. Each profile zeroes the rules that would double-count the same physical fact. Rules with weight 0 are still evaluated and shown as "triggered (unscored in profile)". "Unrelated" means the entities share no UEI or parent UEI and have dissimilar names.

**Capacity benchmarks** (`math_engine.py`) are approximate and cited, and you can override them with `--unit-rate`:

| Benchmark | Default | Applies to |
|---|---|---|
| `child_nutrition_meal` | $4.50 per meal × 2 meals per child-day, 365 days; ceiling 400 children | Assistance Listings 10.558 / 10.559, NAICS 624210 |
| `child_care_subsidy_day` | none (state-specific; you supply it), 365 days; ceiling 400 children | 93.575 / 93.596, NAICS 624410 |
| `hospice_per_diem` | $210 per patient-day, 365 days; ceiling 500 patients | NAICS 623110 / 624120, hospice vertical |

`Estimated Daily Beneficiaries = Award ÷ (Statutory Unit Rate × Units per Day × 365)`. Every default is chosen to *under*-estimate headcount, so a flag means the dollars exceed capacity even on generous assumptions. All 365 days are treated as billable. Child nutrition counts the maximum two reimbursable meals per child-day (a single $4.50 meal rate would overstate headcount about twofold). Multi-year awards are spread over their period of performance. When no state license matches, capacity falls back to a facility-type ceiling, and the dossier says which basis it used.

### DOJ Health Care Fraud Strike Force presets

`--strike-force` (CLI) and the sidebar preset select the federal judicial districts and focus counties to scan. USASpending is queried with state + county FIPS location filters, and each recipient's district is confirmed from the county FIPS in the Census geographies response.

| Preset | Districts | Focus area |
|---|---|---|
| `florida` | S.D. Fla., M.D. Fla. | Miami-Dade, Broward, Palm Beach; Hillsborough, Pinellas, Pasco (Tampa Bay) |
| `texas` | S.D. Tex., N.D. Tex. | Harris, Fort Bend (Houston), Hidalgo, Cameron (McAllen); Dallas, Tarrant, Collin, Denton |
| `west_coast` | C.D. Cal., N.D. Cal., D. Ariz., D. Nev. | Los Angeles, Orange, Riverside, San Bernardino; SF Bay Area counties; Arizona and Nevada statewide |
| `midwest` | E.D. Mich., N.D. Ill. | Wayne, Oakland, Macomb (Detroit); Cook, DuPage, Lake, Will (Chicago) |
| `northeast` | E.D.N.Y., D.N.J., E.D. Pa. | Kings, Queens, Richmond, Nassau, Suffolk; New Jersey statewide; Philadelphia, Montgomery, Delaware, Bucks |
| `gulf_coast` | E.D. La., M.D. La., S.D. Miss. | Orleans, Jefferson, St. Tammany; East Baton Rouge (Baton Rouge is in the *Middle* District of Louisiana); Harrison (Biloxi), Hancock, Jackson |
| `custom` | | Your states, ZIP codes, or nationwide |

The presets are targeting groupings modelled on where the Strike Forces operate, not official DOJ designations. Each dossier's Strike Force section names the district, the U.S. Attorney's Office and how the district was determined.

**Track A/B routing** is a deterministic suggestion. Track A covers entity-level indicators suited to a criminal or OIG referral. Track B covers multi-entity network, exclusion and reimbursement-volume patterns for civil False Claims Act counsel. Counsel makes the actual decision.

## Safeguards

- **Public, lawful sources only.** USASpending, NPPES, Census, OSM Overpass, the published LEIE file and public Socrata datasets are all unauthenticated. SAM.gov, Google Street View and Mapillary are optional and use your own keys. Scrimmy does no scraping and never bypasses logins or CAPTCHAs: state data that sits behind a search UI enters as a CSV you export yourself.
- **Rate limits.** Per-host minimum intervals (1.5 s for Overpass), backoff on 429/5xx with `Retry-After` honoured, and an identifying User-Agent (set `SCRIMMY_CONTACT`).
- **Chain of custody.** Raw response bytes are archived write-once per lead, with SHA-256, the exact URL, the request body and a UTC timestamp. API keys are redacted from recorded URLs. Google imagery is hashed but **not stored**, because Google Maps Platform terms restrict caching. Mapillary imagery (CC BY-SA) is archived with attribution.
- **Neutral language.** Rule text uses evidentiary wording ("Statistically anomalous disbursement-to-capacity ratio", "Unresolved corporate co-location"). LLM prose must pass `LanguageGuard`, which rejects accusatory terms and any number not in the fact sheet; otherwise the template is used and the fallback is recorded.
- **The vision model only describes the image.** Its category and confidence feed a fixed-threshold rule, which also requires recent imagery, an on-site service type and no conflicting OSM commercial evidence. The prompt forbids describing people.
- **No automated submission.** There is no code path that sends anything to an agency, hotline or tip portal.

## Limitations

- Cross-entity rules only see recipients returned by your filters.
- A failed lookup never drops a recipient. Search rows carry the recipient's UEI, name, address ("Recipient Location") and amounts. Profile, award-detail and enrichment failures are recorded as **data gaps**, and the affected rules score as insufficient data. Only recipients with no positive award amount are skipped. Each hosted (serverless) step works within a 48-second time budget (`SCRIMMY_STEP_BUDGET_S`), so retries cannot push it past Vercel's 60-second limit.
- The hosted map uses CARTO basemaps (Positron, or Dark Matter in dark mode); OpenStreetMap's own tile servers block embedded use. CARTO's free basemaps are intended for non-commercial use within their usage limits; check carto.com before heavy or commercial use.
- USASpending has no formation dates, so `NEWLY_FORMED_WINDFALL` needs `--sam-api-key` or `--formation-csv`.
- Prime recipients of pass-through programs (CCDF, CACFP, Medicaid) are often state agencies or sponsors, not site operators. Capacity ratios for sponsors and multi-site operators will look high for lawful reasons.
- OSM coverage varies, Census geocodes are interpolated, and person-name matches can be false positives.
- State licensing datasets vary by state. Verify the dataset id, domain and columns of every source you configure. The Texas entry in the example config could not be verified from the build environment.

## Development

```bash
pip install -r requirements-dev.txt
python -m pytest -q
```

The suite runs fully offline using `httpx.MockTransport` responses shaped like the public API documentation. It also smoke-tests the Streamlit app's initial render with `streamlit.testing`.
