"""CLI entry point.

Example:
    python -m scrimmy --state MN --vertical childcare --min-award 250000 --threshold 50 --output ./dossiers
    python -m scrimmy --demo --output ./demo_dossiers          # offline, synthetic data
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from datetime import date
from pathlib import Path

from . import __version__
from .clients import AWARD_GROUPS, AwardSearchFilters
from .enrichment import EnrichmentConfig, OperatorFormationIndex
from .pipeline import Pipeline, PipelineSettings
from .scorer import ScoringConfig
from .synthesizer import make_backend
from .verticals import VERTICALS

BANNER = (
    "scrimmy {v}: investigative LEAD generation from public federal data.\n"
    "Outputs are unverified leads for human review. This tool never submits reports to any agency.\n"
)
DEFAULT_GROUPS = ["contracts", "grants", "direct_payments", "other"]


def _csv_list(values: list[str] | None) -> list[str]:
    out: list[str] = []
    for v in values or []:
        out += [x.strip() for x in v.split(",") if x.strip()]
    return out


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="scrimmy",
        description="Screen federal award recipients for documented anomaly patterns and produce evidence dossiers for human review.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    g = p.add_argument_group("scan scope (USASpending filters)")
    g.add_argument("--state", help="Recipient state (2-letter postal code), e.g. MN")
    g.add_argument("--zip", action="append", dest="zips", metavar="ZIP", help="Recipient ZIP code(s); repeatable or comma-separated. Overrides --state.")
    g.add_argument("--vertical", choices=sorted(VERTICALS), help="Preset NAICS/PSC/Assistance Listing filters for a sector.")
    g.add_argument("--naics", action="append", help="Additional NAICS codes (contracts).")
    g.add_argument("--psc", action="append", help="Additional PSC codes (contracts).")
    g.add_argument("--program", action="append", help="Additional Assistance Listing (CFDA) numbers, e.g. 93.575 (assistance awards).")
    g.add_argument("--award-types", default=",".join(DEFAULT_GROUPS), help=f"Comma list of award groups: {', '.join(AWARD_GROUPS)}")
    g.add_argument("--min-award", type=float, help="Minimum award amount (USD).")
    g.add_argument("--max-award", type=float, help="Maximum award amount (USD).")
    g.add_argument("--start-date", type=date.fromisoformat, help="Award action period start (YYYY-MM-DD).")
    g.add_argument("--end-date", type=date.fromisoformat, help="Award action period end (YYYY-MM-DD).")
    g.add_argument("--recipient-search", help="Free-text recipient name / UEI filter.")
    g.add_argument("--max-awards", type=int, default=200, help="Maximum award rows to pull (split across award groups).")
    g.add_argument("--detail-awards", type=int, default=3, help="Award-detail lookups per recipient (officers, signature dates).")

    e = p.add_argument_group("enrichment")
    e.add_argument("--sam-api-key", default=os.environ.get("SAM_API_KEY"), help="Optional SAM.gov public API key (env SAM_API_KEY) for entity start dates.")
    e.add_argument("--formation-csv", type=Path, help="CSV of operator-retrieved formation dates (columns: uei,name,formation_date,source_note).")
    e.add_argument("--formation-proxy-npi", action="store_true", help="Use NPI enumeration date as a formation-date proxy when no better source exists.")
    e.add_argument("--npi-all", action="store_true", help="Run NPPES checks on every recipient, not only healthcare-classified ones.")
    e.add_argument("--osm-radius", type=int, default=40, help="Overpass search radius in meters around the geocoded point.")
    e.add_argument("--skip-osm", action="store_true", help="Skip Overpass land-use lookups.")
    e.add_argument("--overpass-url", help="Alternate Overpass endpoint (env SCRIMMY_OVERPASS_URL).")

    s = p.add_argument_group("scoring & output")
    s.add_argument("--threshold", type=int, default=50, help="Minimum score (0-100) for a dossier to be generated.")
    s.add_argument("--newly-formed-days", type=int, default=120)
    s.add_argument("--major-award-min", type=float, default=100_000.0)
    s.add_argument("--residential-award-min", type=float, default=250_000.0)
    s.add_argument("--output", type=Path, default=Path("./dossiers"))
    s.add_argument("--llm-provider", default="auto", choices=["auto", "template", "anthropic", "openai", "ollama", "litellm"],
                   help="Executive-summary author. 'auto' uses ANTHROPIC_API_KEY / OPENAI_API_KEY if set, else the offline template.")
    s.add_argument("--llm-model", help="Model name for the chosen provider (env SCRIMMY_LLM_MODEL).")

    n = p.add_argument_group("network")
    n.add_argument("--timeout", type=float, default=30.0)
    n.add_argument("--max-retries", type=int, default=5, help="Attempts per request (with exponential backoff).")
    n.add_argument("--contact", help="Contact email placed in the User-Agent (courtesy to public API operators; env SCRIMMY_CONTACT).")

    p.add_argument("--demo", action="store_true", help="Run fully offline against bundled SYNTHETIC fixtures.")
    p.add_argument("-v", "--verbose", action="count", default=0)
    p.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose > 1 else logging.INFO if args.verbose else logging.WARNING,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    )
    if args.contact:
        os.environ["SCRIMMY_CONTACT"] = args.contact
    sys.stderr.write(BANNER.format(v=__version__))

    if args.demo:
        from .demo import demo_settings, demo_transport

        settings = demo_settings(args.output, args.threshold)
        transport = demo_transport()
    else:
        if not (args.state or args.zips or args.recipient_search):
            sys.stderr.write("error: provide at least one of --state, --zip or --recipient-search to bound the scan.\n")
            return 2
        groups = [g.strip() for g in args.award_types.split(",") if g.strip()]
        bad = [g for g in groups if g not in AWARD_GROUPS]
        if bad:
            sys.stderr.write(f"error: unknown award type group(s): {', '.join(bad)}\n")
            return 2
        vertical = VERTICALS.get(args.vertical) if args.vertical else None
        filters = AwardSearchFilters(
            state=args.state,
            zip_codes=_csv_list(args.zips),
            naics_codes=sorted(set((vertical.naics_codes if vertical else []) + _csv_list(args.naics))),
            psc_codes=sorted(set((vertical.psc_codes if vertical else []) + _csv_list(args.psc))),
            program_numbers=sorted(set((vertical.program_numbers if vertical else []) + _csv_list(args.program))),
            min_award=args.min_award,
            max_award=args.max_award,
            start_date=args.start_date,
            end_date=args.end_date,
            recipient_search_text=args.recipient_search,
        )
        settings = PipelineSettings(
            filters=filters,
            award_groups=groups,
            max_awards=args.max_awards,
            threshold=args.threshold,
            output_dir=args.output,
            vertical=vertical,
            detail_awards_per_entity=args.detail_awards,
            enrichment=EnrichmentConfig(osm_radius_m=args.osm_radius, npi_for_all=args.npi_all,
                                        formation_proxy_npi=args.formation_proxy_npi, skip_osm=args.skip_osm),
            scoring=ScoringConfig(newly_formed_max_days=args.newly_formed_days, major_award_min=args.major_award_min,
                                  residential_award_min=args.residential_award_min),
            sam_api_key=args.sam_api_key,
            formation_index=OperatorFormationIndex.load(args.formation_csv) if args.formation_csv else None,
            overpass_url=args.overpass_url,
            timeout=args.timeout,
            max_attempts=args.max_retries,
        )
        transport = None

    backend = make_backend(args.llm_provider, args.llm_model)
    pipeline = Pipeline(settings, backend=backend, transport=transport)
    try:
        result = pipeline.run()
    finally:
        pipeline.close()

    for group, err in result.failed_groups.items():
        sys.stderr.write(f"warning: award search for '{group}' failed; results are incomplete: {err}\n")
    if result.failed_groups and len(result.failed_groups) == len(settings.award_groups):
        sys.stderr.write("error: every USASpending award search failed; no recipients were screened. Check network access to api.usaspending.gov.\n")
        return 1

    print(f"\nScreened {len(result.results)} recipient(s); {len(result.leads)} at or above threshold {settings.threshold}.\n")
    print(f"{'Score':>5}  {'Total awarded':>16}  Recipient")
    for r in result.results[:25]:
        marker = "*" if r.dossier else " "
        print(f"{r.score.score:>5}{marker} {r.entity.total_awarded:>16,.2f}  {r.entity.name}")
    if len(result.results) > 25:
        print(f"  ... {len(result.results) - 25} more in manifest")
    print(f"\n* = dossier written. Manifest: {result.manifest_path}\nSource ledger: {result.ledger_path}")
    for r in result.leads:
        print(f"  {r.md_path}")
    print("\nNext step: work each lead through docs/FIELD_INVESTIGATOR_PROTOCOL.md before drafting any tip.")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
