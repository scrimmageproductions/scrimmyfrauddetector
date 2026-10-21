"""CLI entry point.

    python -m scrimmy --state MN --vertical childcare --min-award 250000 --threshold 50 --output ./dossiers
    python -m scrimmy leie-download --output ./data/LEIE.csv
    python -m scrimmy verify-evidence ./dossiers/evidence/SCRIMMY-...
    python -m scrimmy --strike-force florida --fiscal-year 2025 --output ./dossiers
"""

from __future__ import annotations

import argparse
import hashlib
import logging
import os
import sys
from datetime import date
from pathlib import Path

from . import __version__
from .clients import AWARD_GROUPS, EvidenceLedger, ResilientHTTP, SourceAPIError
from .evidence import EvidenceVault
from .leie_matcher import LEIE_DOWNLOAD_URL, LEIEMatcher
from .options import DEFAULT_GROUPS, ScanOptions
from .pipeline import CUSTOM_PRESET_KEY, STRIKE_FORCE_PRESETS, Pipeline
from .scorer import PROFILES
from .synthesizer import make_backend
from .verticals import VERTICALS

BANNER = (
    "scrimmy {v}: investigative LEAD generation from public federal data.\n"
    "Outputs are unverified leads for human review. This tool never submits reports to any agency.\n"
)


def _csv_list(values: list[str] | None) -> list[str]:
    out: list[str] = []
    for v in values or []:
        out += [x.strip() for x in v.split(",") if x.strip()]
    return out


def _rates(values: list[str] | None) -> dict[str, float]:
    out = {}
    for v in values or []:
        key, _, rate = v.partition("=")
        out[key.strip()] = float(rate)
    return out


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="scrimmy",
        description="Screen federal award recipients for documented anomaly patterns and produce evidence dossiers for human review. "
                    "Subcommands: leie-download, verify-evidence.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    g = p.add_argument_group("scan scope (USASpending filters)")
    g.add_argument("--strike-force", choices=[*sorted(STRIKE_FORCE_PRESETS), CUSTOM_PRESET_KEY],
                   help="DOJ Health Care Fraud Strike Force targeting preset (districts + focus counties). 'custom' = use --state/--zip.")
    g.add_argument("--state", action="append", dest="states", metavar="ST", help="Recipient state(s), e.g. MN; repeatable or comma-separated.")
    g.add_argument("--nationwide", action="store_true", help="No geographic filter (bounded by --max-awards).")
    g.add_argument("--zip", action="append", dest="zips", metavar="ZIP", help="Recipient ZIP code(s); repeatable or comma-separated. Overrides --state.")
    g.add_argument("--vertical", choices=sorted(VERTICALS),
                   help="Sector preset. Default: child day care & nutrition (624410, 624210), home health (621610), hospice (623110, 624120).")
    g.add_argument("--naics", action="append", help="Additional NAICS codes (contracts).")
    g.add_argument("--psc", action="append", help="Additional PSC codes (contracts).")
    g.add_argument("--program", action="append", help="Additional Assistance Listing (CFDA) numbers, e.g. 10.558.")
    g.add_argument("--award-types", default=",".join(DEFAULT_GROUPS), help=f"Comma list of award groups: {', '.join(AWARD_GROUPS)}")
    g.add_argument("--min-award", type=float, default=250_000.0, help="Award floor (USD). Use 0 for none.")
    g.add_argument("--max-award", type=float)
    g.add_argument("--fiscal-year", type=int, help="Federal fiscal year (FY2025 = 2024-10-01..2025-09-30). Overrides dates.")
    g.add_argument("--start-date", type=date.fromisoformat)
    g.add_argument("--end-date", type=date.fromisoformat)
    g.add_argument("--recipient-search", help="Free-text recipient name / UEI filter.")
    g.add_argument("--max-awards", type=int, default=200, help="Maximum award rows to pull (split across award groups).")
    g.add_argument("--detail-awards", type=int, default=3, help="Award-detail lookups per recipient (officers, signature dates).")

    e = p.add_argument_group("enrichment")
    e.add_argument("--leie-csv", type=Path, help="HHS-OIG LEIE full database CSV (get it with `scrimmy leie-download`).")
    e.add_argument("--license-sources", type=Path, help="JSON file of state licensing sources (see config/license_sources.example.json).")
    e.add_argument("--unit-rate", action="append", metavar="BENCHMARK=USD", help="Override a capacity benchmark rate, e.g. ccdf_child_day=55.")
    e.add_argument("--capacity-ratio", type=float, default=2.0, help="Flag when implied daily beneficiaries exceed this multiple of licensed capacity.")
    e.add_argument("--sam-api-key", default=os.environ.get("SAM_API_KEY"), help="Optional SAM.gov public API key (env SAM_API_KEY).")
    e.add_argument("--formation-csv", type=Path, help="CSV of operator-retrieved filings: uei,name,formation_date,registered_agent,officers,source_note.")
    e.add_argument("--formation-proxy-npi", action="store_true", help="Use NPI enumeration date as a formation-date proxy.")
    e.add_argument("--npi-all", action="store_true", help="Run NPPES checks on every recipient, not only healthcare-classified ones.")
    e.add_argument("--osm-radius", type=int, default=40)
    e.add_argument("--skip-osm", action="store_true")
    e.add_argument("--overpass-url", help="Alternate Overpass endpoint (env SCRIMMY_OVERPASS_URL).")
    e.add_argument("--vision", action="store_true", help="Street-level imagery + vision-model site check (needs MAPILLARY_TOKEN or GOOGLE_MAPS_API_KEY).")
    e.add_argument("--vision-provider", default="auto", choices=["auto", "anthropic", "openai", "litellm", "ollama"])
    e.add_argument("--vision-model")

    s = p.add_argument_group("scoring & output")
    s.add_argument("--scoring-profile", default="registry", choices=sorted(PROFILES),
                   help="'registry': public-registry signals (zero keys). 'capacity': adds licensed-capacity arithmetic and vision site checks.")
    s.add_argument("--threshold", type=int, default=50, help="Score (0-100) at which a High-Priority Forensic Lead dossier is generated.")
    s.add_argument("--newly-formed-days", type=int, default=120)
    s.add_argument("--major-award-min", type=float, default=100_000.0)
    s.add_argument("--residential-award-min", type=float, default=250_000.0)
    s.add_argument("--output", type=Path, default=Path("./dossiers"))
    s.add_argument("--evidence-dir", type=Path, help="Root for /evidence/<lead_id>/ archives (default: <output>/evidence).")
    s.add_argument("--llm-provider", default="auto", choices=["auto", "template", "anthropic", "openai", "ollama", "litellm"],
                   help="Executive-summary author. 'auto' uses ANTHROPIC_API_KEY / OPENAI_API_KEY if set, else the offline template.")
    s.add_argument("--llm-model")

    n = p.add_argument_group("network")
    n.add_argument("--timeout", type=float, default=30.0)
    n.add_argument("--max-retries", type=int, default=5)
    n.add_argument("--contact", help="Contact email placed in the User-Agent (env SCRIMMY_CONTACT).")

    p.add_argument("-v", "--verbose", action="count", default=0)
    p.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    return p


def cmd_leie_download(argv: list[str]) -> int:
    p = argparse.ArgumentParser(prog="scrimmy leie-download", description="Download the HHS-OIG LEIE full database CSV.")
    p.add_argument("--output", type=Path, default=Path("./data/LEIE_UPDATED.csv"))
    p.add_argument("--url", default=LEIE_DOWNLOAD_URL)
    args = p.parse_args(argv)
    http = ResilientHTTP(ledger=EvidenceLedger(), timeout=120)
    try:
        data, rec = http.request_bytes("hhs_oig_leie", "GET", args.url)
    except SourceAPIError as exc:
        sys.stderr.write(f"error: {exc}\n")
        return 1
    finally:
        http.close()
    matcher = LEIEMatcher.from_bytes(data)  # validates the column layout before saving
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(data)
    meta = args.output.with_suffix(args.output.suffix + ".provenance.json")
    meta.write_text(rec.model_dump_json(indent=2), encoding="utf-8")
    print(f"Saved {len(data):,} bytes, {len(matcher.rows):,} active exclusions -> {args.output}")
    print(f"SHA-256 {hashlib.sha256(data).hexdigest()}  retrieved {rec.retrieved_at:%Y-%m-%d %H:%M:%S} UTC  (provenance: {meta})")
    return 0


def cmd_verify_evidence(argv: list[str]) -> int:
    p = argparse.ArgumentParser(prog="scrimmy verify-evidence", description="Verify a lead's evidence directory against SHA256SUMS.")
    p.add_argument("lead_dir", type=Path, nargs="+")
    args = p.parse_args(argv)
    rc = 0
    for d in args.lead_dir:
        problems = EvidenceVault.verify(d)
        print(f"{'OK  ' if not problems else 'FAIL'} {d}")
        for prob in problems:
            print(f"     {prob}")
        rc |= bool(problems)
    return rc


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] == "leie-download":
        return cmd_leie_download(argv[1:])
    if argv and argv[0] == "verify-evidence":
        return cmd_verify_evidence(argv[1:])

    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose > 1 else logging.INFO if args.verbose else logging.WARNING,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    )
    if args.contact:
        os.environ["SCRIMMY_CONTACT"] = args.contact
    sys.stderr.write(BANNER.format(v=__version__))

    opts = ScanOptions(
        states=_csv_list(args.states), zip_codes=_csv_list(args.zips), strike_force=args.strike_force, nationwide=args.nationwide,
        vertical=args.vertical, naics=_csv_list(args.naics),
        psc=_csv_list(args.psc), programs=_csv_list(args.program),
        award_groups=[g.strip() for g in args.award_types.split(",") if g.strip()],
        min_award=args.min_award or None, max_award=args.max_award, start_date=args.start_date, end_date=args.end_date,
        fiscal_year=args.fiscal_year, recipient_search=args.recipient_search, max_awards=args.max_awards,
        detail_awards=args.detail_awards, threshold=args.threshold, scoring_profile=args.scoring_profile,
        newly_formed_days=args.newly_formed_days, major_award_min=args.major_award_min,
        residential_award_min=args.residential_award_min, capacity_ratio=args.capacity_ratio, unit_rates=_rates(args.unit_rate),
        output_dir=args.output, evidence_dir=args.evidence_dir, sam_api_key=args.sam_api_key, formation_csv=args.formation_csv,
        formation_proxy_npi=args.formation_proxy_npi, leie_csv=args.leie_csv, license_sources=args.license_sources,
        npi_all=args.npi_all, osm_radius=args.osm_radius, skip_osm=args.skip_osm, overpass_url=args.overpass_url,
        vision=args.vision, vision_provider=args.vision_provider, vision_model=args.vision_model,
        timeout=args.timeout, max_retries=args.max_retries,
    )
    errors = opts.validate()
    if errors:
        for err in errors:
            sys.stderr.write(f"error: {err}\n")
        return 2
    try:
        settings = opts.to_settings()
    except (OSError, ValueError) as exc:
        sys.stderr.write(f"error: {exc}\n")
        return 2

    pipeline = Pipeline(settings, backend=make_backend(args.llm_provider, args.llm_model))
    try:
        result = pipeline.run()
    finally:
        pipeline.close()

    for group, err in result.failed_groups.items():
        sys.stderr.write(f"warning: award search for '{group}' failed; results are incomplete: {err}\n")
    if result.failed_groups and len(result.failed_groups) == len(settings.award_groups):
        sys.stderr.write("error: every USASpending award search failed; no recipients were screened. Check network access to api.usaspending.gov.\n")
        return 1

    print(f"\nScreened {len(result.results)} recipient(s) / {result.awards_scanned} award(s); "
          f"{len(result.leads)} High-Priority Forensic Lead(s) at threshold {settings.threshold} (profile '{settings.scoring.profile}').\n")
    print(f"{'Score':>5}  {'Total awarded':>16}  Recipient")
    for r in result.results[:25]:
        print(f"{r.score.score:>5}{'*' if r.dossier else ' '} {r.entity.total_awarded:>16,.2f}  {r.entity.name}")
    if len(result.results) > 25:
        print(f"  ... {len(result.results) - 25} more in manifest")
    print(f"\n* = dossier written. Manifest: {result.manifest_path}\nSource ledger: {result.ledger_path}")
    for r in result.leads:
        print(f"  {r.md_path}\n    evidence: {r.evidence_path}")
    print("\nNext step: work each lead through docs/FIELD_INVESTIGATOR_SOP.md before drafting any tip.")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
