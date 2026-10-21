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
| `scrimmy/evidence.py` | Write-once evidence vault, `SHA256SUMS`, verification |
| `scrimmy/synthesizer.py` | Track A/B routing, statutes, checklists, guarded LLM summary, Markdown/JSON rendering |
| `scrimmy/pipeline.py`, `scrimmy/options.py`, `scrimmy/main.py` | Orchestrator, shared option builder, CLI |
| `app.py` | Streamlit forensic console (live data only) |
| `scrimmy/demo.py` | Offline synthetic fixtures for `--demo` and the tests. Never used by `app.py` |

The briefs named some files differently; they map as follows: `ingestor.py` → `ingestion.py`, `enricher.py` → `enrichment.py`, `dossier.py` → `synthesizer.py` + `evidence.py`, `api_client.py` → `clients.py`, `forensic_engine.py` → `scorer.py` + `math_engine.py`.

## Install

```bash
python3.11 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt            # core + web console
pip install -r requirements-llm.txt        # optional: anthropic / openai / litellm (summaries, vision)
pip install -r requirements-dev.txt        # optional: pytest
```

## Web console

```bash
streamlit run app.py            # opens http://localhost:8501
```

1. In the sidebar, pick verticals (child care, home health, hospice, clinics), a state, an award floor and a fiscal year.
2. Optional: press **Download latest LEIE** to fetch the HHS-OIG exclusion file, which turns on LEIE screening. Upload a licensing-sources JSON (see `config/license_sources.example.json`) to enable capacity arithmetic and switch the profile to **Capacity**.
3. Press **Run live scan**. The console calls USASpending.gov, NPPES, the Census Geocoder and OpenStreetMap live, with progress shown as it goes. A scan of 150 award rows typically takes a few minutes, because Scrimmy throttles itself to respect each public API's rate limits.
4. **Live Scanner** shows summary metrics and lead cards. **Target Inspector** shows the score breakdown, capacity math, the map, raw API payloads and the verification checklist. **Network Map** shows shared addresses and people. **Dossier Export** downloads the Markdown briefing, the JSON provenance file, and the evidence archive (`.zip` with `SHA256SUMS`).

There is no sample data in the console. If a scan finds nothing above the threshold, you get an empty state. If the APIs are unreachable, the console says so.

## CLI

```bash
# Offline demo (synthetic data, no network), both scoring profiles:
python -m scrimmy --demo --output ./demo_dossiers
python -m scrimmy --demo --scoring-profile capacity --output ./demo_dossiers_capacity

# Default targeting (child care/nutrition, home health, hospice, clinics), $250k floor, FY2025:
export SCRIMMY_CONTACT="you@example.org"     # courtesy contact sent in the User-Agent
python -m scrimmy leie-download --output ./data/LEIE_UPDATED.csv
python -m scrimmy --state MN --fiscal-year 2025 --leie-csv ./data/LEIE_UPDATED.csv --output ./dossiers

# Child care with capacity arithmetic from state licensing data:
python -m scrimmy --state TX --vertical childcare --scoring-profile capacity \
  --license-sources config/license_sources.example.json --unit-rate ccdf_child_day=55 --leie-csv ./data/LEIE_UPDATED.csv

# Add street-level site checks (needs an imagery key and a vision model):
MAPILLARY_TOKEN=... ANTHROPIC_API_KEY=... python -m scrimmy --state MN --vertical hospice --scoring-profile capacity --vision

# Verify an evidence archive later:
python -m scrimmy verify-evidence ./dossiers/evidence/SCRIMMY-20261021-XXXXXXXXXXXX
```

Run `python -m scrimmy --help` for every option.

### Outputs

- `<output>/SCRIMMY-<date>-<id>_<name>.{json,md}`: the dossier. The JSON holds the full entity, enrichment, rule evaluations with evidence, capacity formula, network DOT, track assessment and every cited `SourceRecord`. The Markdown is the briefing, with sections covering the Executive Summary & Lead Classification, Entity Identity & Federal Award Summary, Flagged Discrepancies, Evidence Matrix, Disbursement-to-Capacity Arithmetic, Physical Site Verification, LEIE Screening, Network Summary, Statutory Frameworks, Ground-Truthing Field Checklist, Final Pre-Filing Review, Caveats, and Chain of Custody.
- `<output>/evidence/<lead_id>/`: write-once raw API responses (byte-for-byte), `ledger.jsonl`, `manifest.json`, `dossier.json`, `dossier.md` and `SHA256SUMS` (check with `sha256sum -c SHA256SUMS`).
- `<output>/scan_manifest_<ts>.json` and `source_ledger_<ts>.jsonl`: every screened entity and every HTTP exchange in the scan.

Samples, generated unedited by `--demo` from fictional data: [`examples/sample_dossier.md`](examples/sample_dossier.md) (registry profile), [`examples/sample_dossier_capacity.md`](examples/sample_dossier_capacity.md) (capacity profile), and [`examples/sample_evidence/`](examples/sample_evidence/) (a full evidence archive).

## Scoring (deterministic, no LLM)

`score = min(100, Σ weightᵢ × triggeredᵢ)`. Every rule reports `triggered`, `not_triggered`, `insufficient_data` or `not_applicable`, with a rationale and evidence. Missing data **never** adds points. Leads at or above `--threshold` (default 50) are marked **High-Priority Forensic Leads**.

| Rule ID | Shown as | registry | capacity | Triggers when |
|---|---|---:|---:|---|
| `LEIE_EXCLUSION_MATCH` | Match to HHS-OIG LEIE | 40 | 35 | The entity, an officer, an authorized official or a registered agent matches an active LEIE row by NPI, or by name + state + (city, ZIP or middle initial). Name + state alone is reported for review but never scored. |
| `MATHEMATICAL_IMPOSSIBILITY` | Statistically Anomalous Disbursement-to-Capacity Ratio | 0 | 40 | (annualized award ÷ (unit rate × operating days)) ÷ licensed capacity > 2.0 |
| `PHANTOM_HEALTHCARE_PROVIDER` | Healthcare Recipient Without Active Organizational NPI | 30 | 20 | A healthcare-classified recipient has no active Type-2 NPI matching its name in its state |
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
| `cacfp_child_day` | $8.40 per child-day (breakfast + lunch + snack), 250 days | Assistance Listing 10.558, NAICS 624210 |
| `ccdf_child_day` | none (state-specific; you supply it) | 93.575 / 93.596, NAICS 624410 |
| `hospice_patient_day` | $220 per patient-day, 365 days | NAICS 623110 / 624120, hospice vertical |

The child-care benchmark is per child-day, not per meal. Each child typically receives several meals a day, so dividing by a single meal rate would overstate headcount roughly threefold.

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
