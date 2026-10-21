# Scrimmy Fraud Detector

Scrimmy is an open-source, deterministic **lead-generation** pipeline. It screens recipients of federal awards, grants and program funds for documented anomaly patterns, then writes evidence dossiers for **human** forensic review.

> Scrimmy produces *leads*, not findings. It never submits reports, tips or complaints anywhere. Every output is an unverified starting point that a person must check with primary records and the [Human Field Investigator Protocol](docs/FIELD_INVESTIGATOR_PROTOCOL.md) before taking any action.

## Pipeline

```
 ┌───────────────┐   ┌──────────────────────┐   ┌────────────────────┐   ┌──────────────────────┐
 │ 1. INGESTION  │──▶│ 2. ENRICHMENT        │──▶│ 3. DETERMINISTIC   │──▶│ 4. SYNTHESIS         │
 │ USASpending   │   │ NPPES NPI Registry   │   │    SCORER (0-100)  │   │ JSON dossier         │
 │ awards ->     │   │ Census Geocoder      │   │ 5 weighted rules,  │   │ Markdown briefing    │
 │ entities      │   │ OSM Overpass land use│   │ full audit trail   │   │ (template or LLM     │
 │ (+ profiles,  │   │ (opt) SAM.gov / CSV  │   │ no LLM involvement │   │  prose, guarded)     │
 │  officers)    │   │  formation dates     │   │                    │   │                      │
 └───────────────┘   └──────────────────────┘   └────────────────────┘   └──────────────────────┘
          every HTTP exchange ──▶ EvidenceLedger (URL, timestamp, SHA-256, raw JSON) ──▶ dossier.sources
```

| Module | Role |
|---|---|
| `scrimmy/models.py` | Strict Pydantic v2 schemas: `AwardeeEntity`, `EnrichmentData`, `AnomalyFlag`, `FraudRiskDossier`, `SourceRecord`, and more |
| `scrimmy/clients.py` | `httpx` clients with per-host throttling, retries with exponential backoff and jitter, `Retry-After` support, timeouts, and the evidence ledger |
| `scrimmy/normalize.py` | Entity/person/address normalization (USPS suffixes, units, PO Box/PMB) and fuzzy matching (`rapidfuzz`, falling back to `difflib`) |
| `scrimmy/ingestion.py` | Stage 1: award search, entity resolution, recipient profile and award-detail hydration |
| `scrimmy/enrichment.py` | Stage 2: NPI matching, geocoding, OSM land-use classification, formation dates |
| `scrimmy/scorer.py` | Stage 3: `DeterministicScorer` |
| `scrimmy/synthesizer.py` | Stage 4: pluggable LLM backends, `LanguageGuard`, Markdown/JSON rendering, statutes, checklists |
| `scrimmy/pipeline.py` | Orchestration, scan manifest, source ledger export |
| `scrimmy/main.py` | CLI |
| `scrimmy/verticals.py` | Sector presets (NAICS / PSC / Assistance Listing numbers) |
| `scrimmy/demo.py` | Offline synthetic fixtures (used by `--demo` and the tests) |

## Install

```bash
python3.11 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt            # core: httpx, pydantic, rapidfuzz
pip install -r requirements-llm.txt        # optional: anthropic / openai / litellm
pip install -r requirements-dev.txt        # optional: pytest
```

## Usage

```bash
# Try it offline first (synthetic data, no network):
python -m scrimmy --demo --output ./demo_dossiers

# Real scan: Minnesota child care recipients with >= $250k awards, dossiers for scores >= 50
export SCRIMMY_CONTACT="you@example.org"     # courtesy contact sent in the User-Agent
python -m scrimmy --state MN --vertical childcare --min-award 250000 --threshold 50 --output ./dossiers

# Narrower: specific ZIPs, an explicit NAICS code and Assistance Listing, a date window
python -m scrimmy --zip 55407,55411 --naics 621610 --program 93.778 \
  --start-date 2024-10-01 --end-date 2025-09-30 --max-awards 300 -v

# Enable the Newly Formed Windfall rule with a formation-date source
python -m scrimmy --state MN --vertical home_health --sam-api-key "$SAM_API_KEY"
python -m scrimmy --state MN --vertical home_health --formation-csv examples/formation_dates.example.csv

# LLM-written executive summary (numbers and evidence stay deterministic)
ANTHROPIC_API_KEY=... python -m scrimmy --state MN --vertical healthcare --llm-provider anthropic
python -m scrimmy --state MN --vertical healthcare --llm-provider ollama --llm-model llama3.1
python -m scrimmy --state MN --vertical healthcare --llm-provider litellm --llm-model anthropic/claude-sonnet-5-5
```

Run `python -m scrimmy --help` for every option. Verticals: `childcare`, `healthcare`, `home_health`, `behavioral_health`, `food_nutrition`, `housing`. Each preset applies NAICS/PSC codes to contract searches and Assistance Listing numbers to grant and assistance searches. These are separate requests, because applying both filters to one request would match nothing.

### Outputs (in `--output`)

- `SCRIMMY-<date>-<id>_<name>.json`: the machine-readable `FraudRiskDossier`, with the entity, enrichment, every rule evaluation and its evidence, the scoring formula, and every cited `SourceRecord` (exact URL, request body, timestamp, attempts, SHA-256 of the full response, raw JSON snapshot).
- `SCRIMMY-<date>-<id>_<name>.md`: the investigator briefing, with an Executive Summary, Entity Profile, Score Breakdown, Evidence Matrix, Specific Inconsistencies, Related Entities, Statutory Cross-References, Ground-Truthing Checklist, Caveats, and Source Ledger.
- `scan_manifest_<ts>.json`: the scan parameters, scoring config, and every screened entity's score and rule statuses, including entities below the threshold.
- `source_ledger_<ts>.jsonl`: every HTTP exchange made during the scan.

See [`examples/sample_dossier.md`](examples/sample_dossier.md) and [`examples/sample_dossier.json`](examples/sample_dossier.json). Both were generated unedited by `--demo` from fictional data.

## Scoring (deterministic, no LLM)

`score = min(100, Σ weightᵢ × triggeredᵢ)`. Every rule reports one of `triggered`, `not_triggered`, `insufficient_data` or `not_applicable`, with a rationale and evidence. Missing data **never** adds points.

| ID | Rule | Weight | Triggers when |
|---|---|---:|---|
| R1 | Newly Formed Windfall | 30 | A single award ≥ `--major-award-min` (default $100k) has an effective date (signed, else start) between 0 and 119 days after the formation date. Needs a formation source: `--sam-api-key`, `--formation-csv`, or opt-in `--formation-proxy-npi`. |
| R2 | High-Dollar Residential / Mail-Drop Mismatch | 25 | Total awarded > `--residential-award-min` (default $250k) **and** the address is a P.O. Box, carries a PMB designator, is a unit address next to a mapped mail-drop storefront (CMRA), or resolves to a residential-tagged OSM structure with no business POIs nearby. |
| R3 | Healthcare Recipient Without Active Organizational NPI | 25 | The recipient is healthcare-classified (by vertical, NAICS 621/622/623, or business type) and no active Type-2 NPI in NPPES matches its name (fuzzy ≥ 90, or ≥ 80 with an address match) in its state. |
| R4 | Anomalous Multi-Entity Co-location | 20 | At least 3 *unrelated* awardees in the scan share the exact normalized street + unit + ZIP. Entities sharing a UEI or parent UEI, or with similar names, count as one group. |
| R5 | Officer Shared Across Disconnected Awardees | 15 | A normalized officer name (USASpending executive compensation, or NPPES authorized official) appears on ≥ 2 unrelated awardees in the same city and state. |

All thresholds and weights are in `ScoringConfig` and are serialized into each scan manifest.

## Safeguards

- **Official, unauthenticated public APIs only:** USASpending.gov, CMS NPPES, the Census Geocoder, and OSM Overpass (low-rate, small-radius, read-only queries). SAM.gov is used only with the operator's own free key. Scrimmy does no scraping and never bypasses logins or CAPTCHAs.
- **Rate limits are respected:** per-host minimum intervals (1.5 s for Overpass), backoff on 429/5xx with `Retry-After` honored, and an identifying User-Agent with a contact address (set `SCRIMMY_CONTACT`).
- **Chain of custody:** each piece of evidence links to a `SourceRecord` holding the exact URL, request body, UTC timestamp, attempt count, SHA-256 of the full response body, and a raw JSON snapshot. Secrets such as the SAM API key are redacted from recorded URLs.
- **Non-defamatory language:** rule rationales use neutral evidentiary wording. LLM output must pass `LanguageGuard`, which rejects accusatory terms ("is a fraud", "stole", "criminal", ...) and any number not present in the fact sheet. Rejected output falls back to the deterministic template, and the fallback is recorded in `narrative_provenance`.
- **No automated submission.** Scrimmy has no code path that sends anything to an agency, hotline or tip portal.

## Limitations

- Cross-entity rules see only the recipients your filters return. A wider scan sees more co-location and officer overlap.
- USASpending does not publish formation dates, so R1 reports `insufficient_data` unless you supply a source.
- OSM coverage varies. The residential signal comes from building and land-use tags, not legal zoning.
- Census geocodes are interpolated along street segments.
- Name-only matching of people produces false positives on common names.
- Prime recipients of pass-through programs (CCDF, CACFP, Medicaid) are often state agencies or sponsors, not the site operators.

## Development

```bash
pip install -r requirements-dev.txt
python -m pytest -q
```

The test suite runs fully offline. It mocks every API with `httpx.MockTransport` and response shapes that follow the public API documentation.
