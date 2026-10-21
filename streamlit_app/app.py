"""Scrimmy Forensic Workstation: Streamlit front end over ``scrimmy.pipeline``.

    pip install -r streamlit_app/requirements.txt
    streamlit run streamlit_app/app.py

Runs anywhere a long-lived process can (local desktop, Streamlit Community Cloud, Render,
Railway, Fly.io). Vercel cannot host Streamlit, which needs a persistent WebSocket; the
Vercel deployment serves the same workflow from public/ + api/index.py.

Every scan makes live HTTP calls: USASpending.gov awards, CMS NPPES, the Census Geocoder
(one-line geographies), OpenStreetMap Overpass, plus HHS-OIG LEIE, state licensing and
imagery when configured. There is no sample, cached or fabricated data in this app. A
scan that returns nothing shows an empty state; unreachable APIs show the error.
"""

from __future__ import annotations

import html
import io
import json
import os
import sys
import zipfile
from datetime import date, datetime, timezone
from pathlib import Path

import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # repo root, so `scrimmy` imports when run from anywhere

from scrimmy import __version__  # noqa: E402
from scrimmy.clients import EvidenceLedger, ResilientHTTP, SourceAPIError  # noqa: E402
from scrimmy.dossier import source_record_ids  # noqa: E402
from scrimmy.leie_matcher import LEIE_DOWNLOAD_URL, LEIEMatcher  # noqa: E402
from scrimmy.models import RuleStatus, SourceRecord  # noqa: E402
from scrimmy.options import ScanOptions  # noqa: E402
from scrimmy.pipeline import CUSTOM_PRESET_KEY, STRIKE_FORCE_PRESETS, EntityResult, Pipeline, ScanResult  # noqa: E402
from scrimmy.scorer import (  # noqa: E402
    ALL_RULES,
    CO_LOCATED_SHELL_CLUSTER,
    EXECUTIVE_SERIAL_REGISTRATION,
    HIGH_DOLLAR_RESIDENTIAL_ZONE,
    LEIE_EXCLUSION_MATCH,
    MATHEMATICAL_IMPOSSIBILITY,
    NEWLY_FORMED_WINDFALL,
    PHANTOM_HEALTHCARE_PROVIDER,
    PHYSICAL_SITE_MISMATCH,
    UNRESOLVED_PHYSICAL_DISCREPANCY,
)
from scrimmy.synthesizer import Synthesizer, make_backend  # noqa: E402
from scrimmy.verticals import PRIORITY_VERTICALS, VERTICALS  # noqa: E402

st.set_page_config(page_title="Scrimmy Forensic Workstation", page_icon="◎", layout="wide", initial_sidebar_state="expanded")

DATA_DIR = Path(os.environ.get("SCRIMMY_DATA_DIR", "./data"))
OUTPUT_ROOT = Path(os.environ.get("SCRIMMY_OUTPUT_DIR", "./dossiers"))
LEIE_PATH = DATA_DIR / "LEIE_UPDATED.csv"

STATES = ["AL", "AK", "AZ", "AR", "CA", "CO", "CT", "DE", "DC", "FL", "GA", "HI", "ID", "IL", "IN", "IA", "KS", "KY", "LA",
          "ME", "MD", "MA", "MI", "MN", "MS", "MO", "MT", "NE", "NV", "NH", "NJ", "NM", "NY", "NC", "ND", "OH", "OK", "OR",
          "PA", "RI", "SC", "SD", "TN", "TX", "UT", "VT", "VA", "WA", "WV", "WI", "WY", "PR", "GU", "VI", "AS", "MP"]
AWARD_FLOORS = {"$100K": 100_000, "$250K": 250_000, "$500K": 500_000, "$1M": 1_000_000, "$2.5M": 2_500_000, "$5M+": 5_000_000}
VIEWS = ["Strike Force Scanner", "Lead Inspector", "Anomaly Matrix", "Dossier Export"]
VERTICAL_LABELS = {"childcare": "Child Day Care & Nutrition", "home_health": "Home Health", "hospice": "Hospice", "clinics": "Clinics"}

# Status pills: Apple system colors (light / dark variants handled in CSS). Short labels for the investigator's view;
# dossiers use the neutral rule titles.
PILLS = {
    MATHEMATICAL_IMPOSSIBILITY: ("Capacity Impossibility", "red"),
    LEIE_EXCLUSION_MATCH: ("LEIE Exclusion Match", "red"),
    PHANTOM_HEALTHCARE_PROVIDER: ("Missing NPI Provider", "orange"),
    HIGH_DOLLAR_RESIDENTIAL_ZONE: ("Residential / Mail Drop", "orange"),
    PHYSICAL_SITE_MISMATCH: ("Site Mismatch", "orange"),
    NEWLY_FORMED_WINDFALL: ("Newly Formed", "yellow"),
    CO_LOCATED_SHELL_CLUSTER: ("Co-located Cluster", "purple"),
    EXECUTIVE_SERIAL_REGISTRATION: ("Serial Officer", "indigo"),
    UNRESOLVED_PHYSICAL_DISCREPANCY: ("Unresolved Address", "gray"),
}
MATRIX_COLS = {
    MATHEMATICAL_IMPOSSIBILITY: "Capacity", LEIE_EXCLUSION_MATCH: "LEIE", PHANTOM_HEALTHCARE_PROVIDER: "NPI",
    HIGH_DOLLAR_RESIDENTIAL_ZONE: "Residential", PHYSICAL_SITE_MISMATCH: "Site", NEWLY_FORMED_WINDFALL: "New entity",
    CO_LOCATED_SHELL_CLUSTER: "Co-location", EXECUTIVE_SERIAL_REGISTRATION: "Officers", UNRESOLVED_PHYSICAL_DISCREPANCY: "Address",
}

CSS = """
<style>
:root {
  --canvas: #F5F5F7; --surface: #FFFFFF; --surface-glass: rgba(255,255,255,0.80); --label: #1D1D1F;
  --secondary: #6E6E73; --tertiary: #86868B; --hairline: rgba(0,0,0,0.06); --shadow: 0 4px 24px rgba(0,0,0,0.04);
  --accent: #0071E3; --red: #FF3B30; --orange: #FF9500; --yellow: #FFCC00; --green: #34C759; --purple: #AF52DE;
  --indigo: #5856D6; --gray: #8E8E93; --fill: rgba(120,120,128,0.12);
}
@media (prefers-color-scheme: dark) {
  :root {
    --canvas: #1C1C1E; --surface: #2C2C2E; --surface-glass: rgba(44,44,46,0.72); --label: #F5F5F7; --secondary: #AEAEB2;
    --tertiary: #8E8E93; --hairline: rgba(255,255,255,0.08); --shadow: 0 4px 24px rgba(0,0,0,0.35); --accent: #0A84FF;
    --red: #FF453A; --orange: #FF9F0A; --yellow: #FFD60A; --green: #30D158; --purple: #BF5AF2; --indigo: #5E5CE6;
    --gray: #98989D; --fill: rgba(120,120,128,0.24);
  }
}
html, body, [class*="css"], .stApp, .stMarkdown, button, input, select, textarea {
  font-family: -apple-system, BlinkMacSystemFont, "SF Pro Text", "SF Pro Display", "Inter", "Helvetica Neue", sans-serif !important;
  -webkit-font-smoothing: antialiased;
}
.stApp { background: var(--canvas); color: var(--label); }
[data-testid="stHeader"] { background: var(--surface-glass); backdrop-filter: saturate(180%) blur(20px); -webkit-backdrop-filter: saturate(180%) blur(20px); }
[data-testid="stSidebar"] { background: var(--surface-glass); backdrop-filter: blur(20px); -webkit-backdrop-filter: blur(20px); border-right: 1px solid var(--hairline); }
.block-container { padding-top: 4.5rem; max-width: 1280px; }
h1, h2, h3 { letter-spacing: -0.022em; color: var(--label); }
.sc-title { font-size: 34px; font-weight: 700; letter-spacing: -0.03em; line-height: 1.1; margin: 0; }
.sc-sub { color: var(--secondary); font-size: 15px; margin: 6px 0 18px 0; line-height: 1.45; }
.sc-card { background: var(--surface); border: 1px solid var(--hairline); border-radius: 16px; box-shadow: var(--shadow); padding: 18px 20px; margin-bottom: 14px; }
.sc-metric-label { color: var(--secondary); font-size: 13px; font-weight: 500; letter-spacing: -0.005em; }
.sc-metric-value { font-size: 30px; font-weight: 650; letter-spacing: -0.025em; margin-top: 4px; font-variant-numeric: tabular-nums; }
.sc-lead { display: flex; align-items: center; gap: 18px; }
.sc-lead-main { flex: 1; min-width: 0; }
.sc-lead-name { font-size: 17px; font-weight: 600; letter-spacing: -0.015em; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.sc-lead-meta { color: var(--secondary); font-size: 13px; margin-top: 3px; }
.sc-amount { font-size: 17px; font-weight: 600; font-variant-numeric: tabular-nums; text-align: right; }
.sc-amount-sub { color: var(--tertiary); font-size: 12px; text-align: right; }
.sc-pill { display: inline-block; padding: 3px 10px; border-radius: 999px; font-size: 12px; font-weight: 600; margin: 8px 6px 0 0; letter-spacing: -0.005em; }
.sc-pill.red { background: color-mix(in srgb, var(--red) 14%, transparent); color: var(--red); }
.sc-pill.orange { background: color-mix(in srgb, var(--orange) 16%, transparent); color: var(--orange); }
.sc-pill.yellow { background: color-mix(in srgb, var(--yellow) 22%, transparent); color: color-mix(in srgb, var(--yellow) 55%, var(--label)); }
.sc-pill.purple { background: color-mix(in srgb, var(--purple) 14%, transparent); color: var(--purple); }
.sc-pill.indigo { background: color-mix(in srgb, var(--indigo) 14%, transparent); color: var(--indigo); }
.sc-pill.gray { background: var(--fill); color: var(--secondary); }
.sc-pill.green { background: color-mix(in srgb, var(--green) 16%, transparent); color: var(--green); }
.sc-ring { flex: 0 0 auto; }
.sc-ring text { font-family: -apple-system, BlinkMacSystemFont, "SF Pro Display", "Inter", sans-serif; font-weight: 650; fill: var(--label); }
.sc-empty { text-align: center; padding: 56px 20px; }
.sc-empty-icon { font-size: 40px; color: var(--tertiary); }
.sc-empty-title { font-size: 20px; font-weight: 600; margin-top: 10px; letter-spacing: -0.02em; }
.sc-empty-sub { color: var(--secondary); font-size: 14px; margin-top: 6px; }
.sc-kv { display: grid; grid-template-columns: 180px 1fr; gap: 6px 16px; font-size: 14px; }
.sc-kv div:nth-child(odd) { color: var(--secondary); }
.sc-section { font-size: 13px; font-weight: 600; color: var(--secondary); text-transform: uppercase; letter-spacing: 0.04em; margin: 4px 0 10px 0; }
.sc-notice { font-size: 12.5px; color: var(--tertiary); line-height: 1.5; }
div[data-testid="stExpander"] { border-radius: 14px; border: 1px solid var(--hairline); background: var(--surface); }
.stButton > button, .stDownloadButton > button { border-radius: 980px; font-weight: 600; padding: 0.45rem 1.2rem; border: 1px solid var(--hairline); }
.stButton > button[kind="primary"] { background: var(--accent); border-color: var(--accent); }
[data-baseweb="select"] > div, .stTextInput input, .stNumberInput input { border-radius: 10px !important; }
</style>
"""

EXTRA_CSS = """
<style>
.sc-matrix { width: 100%; border-collapse: separate; border-spacing: 0; font-size: 13px; }
.sc-matrix th { position: sticky; top: 0; background: var(--surface); color: var(--secondary); font-weight: 500; text-align: center;
  padding: 10px 6px; border-bottom: 1px solid var(--hairline); white-space: nowrap; }
.sc-matrix th:first-child, .sc-matrix td:first-child { text-align: left; }
.sc-matrix td { padding: 9px 6px; border-bottom: 1px solid var(--hairline); text-align: center; vertical-align: middle; }
.sc-matrix tr:last-child td { border-bottom: 0; }
.sc-cell { display: inline-block; min-width: 34px; padding: 2px 8px; border-radius: 999px; font-weight: 600; font-size: 12px; font-variant-numeric: tabular-nums; }
.sc-cell.hit { background: color-mix(in srgb, var(--red) 15%, transparent); color: var(--red); }
.sc-cell.hit-0 { background: var(--fill); color: var(--secondary); }
.sc-cell.no { color: var(--tertiary); }
.sc-cell.na { color: color-mix(in srgb, var(--tertiary) 50%, transparent); }
.sc-cell.insuf { color: var(--orange); }
.sc-score { font-weight: 650; font-variant-numeric: tabular-nums; }
.sc-legend { font-size: 12px; color: var(--secondary); margin-top: 10px; }
.sc-legend span { margin-right: 14px; }
.sc-district { font-size: 12px; color: var(--secondary); }
.sc-scroll { overflow-x: auto; }
.sc-matrix th, .sc-matrix td { border-left: 0 !important; border-right: 0 !important; border-top: 0 !important; }
</style>
"""



# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def esc(text: object) -> str:
    return html.escape("" if text is None else str(text))


def money(x: float) -> str:
    if x >= 1_000_000:
        return f"${x / 1_000_000:,.2f}M"
    if x >= 1_000:
        return f"${x / 1_000:,.0f}K"
    return f"${x:,.0f}"


def ring(score: int, size: int = 58) -> str:
    color = "var(--red)" if score >= 75 else "var(--orange)" if score >= 50 else "var(--yellow)" if score >= 25 else "var(--gray)"
    r = size / 2 - 5
    circ = 2 * 3.14159 * r
    dash = circ * score / 100
    return (f'<svg class="sc-ring" width="{size}" height="{size}" viewBox="0 0 {size} {size}" role="img" aria-label="score {score}">'
            f'<circle cx="{size/2}" cy="{size/2}" r="{r}" fill="none" stroke="var(--fill)" stroke-width="6"/>'
            f'<circle cx="{size/2}" cy="{size/2}" r="{r}" fill="none" stroke="{color}" stroke-width="6" stroke-linecap="round" '
            f'stroke-dasharray="{dash:.1f} {circ:.1f}" transform="rotate(-90 {size/2} {size/2})"/>'
            f'<text x="50%" y="54%" text-anchor="middle" dominant-baseline="middle" font-size="{size*0.3:.0f}">{score}</text></svg>')


def pills(result: EntityResult) -> str:
    out = []
    for ev in result.score.evaluations:
        if ev.status is RuleStatus.TRIGGERED and ev.points_awarded > 0:
            label, color = PILLS.get(ev.rule_id, (ev.title, "gray"))
            out.append(f'<span class="sc-pill {color}">{esc(label)} +{ev.points_awarded}</span>')
    return "".join(out) or '<span class="sc-pill gray">No scored indicators</span>'


def card(body: str) -> None:
    st.markdown(f'<div class="sc-card">{body}</div>', unsafe_allow_html=True)


def empty_state(icon: str, title: str, sub: str) -> None:
    card(f'<div class="sc-empty"><div class="sc-empty-icon">{icon}</div><div class="sc-empty-title">{esc(title)}</div>'
         f'<div class="sc-empty-sub">{esc(sub)}</div></div>')


def lead_card(r: EntityResult, size: int = 58) -> None:
    e = r.entity
    sf = r.enrichment.strike_force
    district = f'<div class="sc-district">{esc(sf.district)}</div>' if sf and sf.district else ""
    card(f'<div class="sc-lead">{ring(r.score.score, size)}<div class="sc-lead-main"><div class="sc-lead-name">{esc(e.name)}</div>'
         f'<div class="sc-lead-meta">{esc(e.address.one_line or "No address published")} · UEI {esc(e.uei or "n/a")} · '
         f'{len(e.awards)} award{"s" if len(e.awards) != 1 else ""}</div>{district}<div>{pills(r)}</div></div>'
         f'<div><div class="sc-amount">{money(e.total_awarded)}</div><div class="sc-amount-sub">awarded in scope</div></div></div>')


@st.cache_resource(show_spinner=False)
def load_leie(path: str, mtime: float) -> LEIEMatcher:  # mtime busts the cache when the file changes
    return LEIEMatcher.from_csv(path)


def download_leie() -> str:
    http = ResilientHTTP(ledger=EvidenceLedger(), timeout=180)
    try:
        data, rec = http.request_bytes("hhs_oig_leie", "GET", LEIE_DOWNLOAD_URL)
    finally:
        http.close()
    matcher = LEIEMatcher.from_bytes(data)
    LEIE_PATH.parent.mkdir(parents=True, exist_ok=True)
    LEIE_PATH.write_bytes(data)
    LEIE_PATH.with_suffix(".csv.provenance.json").write_text(rec.model_dump_json(indent=2), encoding="utf-8")
    return f"Saved {len(matcher.rows):,} active exclusions (SHA-256 {rec.response_sha256[:12]}…)"


def zip_dir(path: Path) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for p in sorted(path.rglob("*")):
            if p.is_file():
                zf.write(p, p.relative_to(path.parent).as_posix())
    return buf.getvalue()


# --------------------------------------------------------------------------- #
# Sidebar: Strike Force targeting
# --------------------------------------------------------------------------- #


def sidebar() -> tuple[ScanOptions, str, bool]:
    sb = st.sidebar
    sb.markdown('<div class="sc-section">Strike Force targeting</div>', unsafe_allow_html=True)
    preset_keys = [*STRIKE_FORCE_PRESETS, CUSTOM_PRESET_KEY]
    preset = sb.selectbox("Preset", preset_keys, index=0,
                          format_func=lambda k: STRIKE_FORCE_PRESETS[k].label if k in STRIKE_FORCE_PRESETS else "Custom / Nationwide")
    states: list[str] = []
    nationwide = False
    if preset in STRIKE_FORCE_PRESETS:
        p = STRIKE_FORCE_PRESETS[preset]
        sb.caption(f"{', '.join(p.metros)}  \n{'; '.join(p.districts)}  \n{len(p.counties)} focus counties"
                   + (f" + {', '.join(sorted(p.whole_states))} statewide" if p.whole_states else ""))
    else:
        nationwide = sb.toggle("Nationwide", value=False, help="No geographic filter; bounded by the number of award rows pulled.")
        states = sb.multiselect("States", STATES, default=[] if nationwide else ["MN"], disabled=nationwide)

    sb.markdown('<div class="sc-section">Targets</div>', unsafe_allow_html=True)
    verticals = sb.pills("Verticals", [*PRIORITY_VERTICALS, "clinics"], default=PRIORITY_VERTICALS, selection_mode="multi",
                         format_func=lambda k: VERTICAL_LABELS.get(k, k))
    floor = sb.select_slider("Minimum award", list(AWARD_FLOORS), value="$250K")
    this_fy = date.today().year + (1 if date.today().month >= 10 else 0)
    fy = sb.selectbox("Fiscal year", ["All years", *range(this_fy, 2007, -1)], index=2,  # default: last complete FY
                      format_func=lambda y: y if y == "All years" else f"FY{y}")
    max_awards = sb.slider("Award rows to pull", 25, 500, 150, step=25)

    sb.markdown('<div class="sc-section">Scoring</div>', unsafe_allow_html=True)
    profile = sb.segmented_control("Profile", ["registry", "capacity"], default="registry",
                                   format_func=lambda p: "Registry" if p == "registry" else "Capacity")
    threshold = sb.slider("Anomaly score threshold", 0, 100, 50, step=5)

    sb.markdown('<div class="sc-section">Cross-references</div>', unsafe_allow_html=True)
    use_osm = sb.toggle("OpenStreetMap land use", value=True)
    have_leie = LEIE_PATH.exists()
    use_leie = sb.toggle("HHS-OIG LEIE screening", value=have_leie, disabled=not have_leie,
                         help=None if have_leie else "Download the LEIE file below first.")
    if sb.button("Download latest LEIE", width="stretch"):
        with sb.status("Downloading HHS-OIG LEIE…"):
            try:
                sb.success(download_leie())
            except (SourceAPIError, ValueError) as exc:
                sb.error(f"LEIE download failed: {exc}")
        st.rerun()
    lic_file = sb.file_uploader("State licensing sources (JSON)", type=["json"],
                                help="See config/license_sources.example.json. Without it, capacity is compared with a facility-type ceiling.")
    have_imagery = bool(os.environ.get("MAPILLARY_TOKEN") or os.environ.get("GOOGLE_MAPS_API_KEY"))
    have_vision_llm = bool(os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("OPENAI_API_KEY"))
    use_vision = sb.toggle("Street-level vision check", value=False, disabled=not (have_imagery and have_vision_llm),
                           help="Needs MAPILLARY_TOKEN or GOOGLE_MAPS_API_KEY, plus ANTHROPIC_API_KEY or OPENAI_API_KEY.")
    llm = sb.selectbox("Summary author", ["template", "auto", "anthropic", "openai", "ollama"], index=0,
                       help="Prose only. Scores and evidence are always deterministic.")
    contact = sb.text_input("Contact email (User-Agent)", value=os.environ.get("SCRIMMY_CONTACT", ""),
                            help="Courtesy identification for public API operators.")
    if contact:
        os.environ["SCRIMMY_CONTACT"] = contact

    lic_path = None
    if lic_file is not None:
        lic_path = DATA_DIR / "uploaded_license_sources.json"
        lic_path.parent.mkdir(parents=True, exist_ok=True)
        lic_path.write_bytes(lic_file.getvalue())

    selected = verticals or []
    opts = ScanOptions(
        strike_force=preset if preset in STRIKE_FORCE_PRESETS else None,
        states=states,
        nationwide=nationwide,
        vertical=selected[0] if len(selected) == 1 else None,
        naics=sorted({c for k in selected for c in VERTICALS[k].naics_codes}) if len(selected) > 1 else [],
        programs=sorted({c for k in selected for c in VERTICALS[k].program_numbers}) if len(selected) > 1 else [],
        min_award=AWARD_FLOORS[floor],
        fiscal_year=None if fy == "All years" else int(fy),
        max_awards=max_awards,
        threshold=threshold,
        scoring_profile=profile or "registry",
        skip_osm=not use_osm,
        leie_csv=LEIE_PATH if use_leie and have_leie else None,
        license_sources=lic_path,
        vision=use_vision,
        output_dir=OUTPUT_ROOT / f"app_{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}",
    )
    sb.markdown(f'<div class="sc-notice">scrimmy {__version__}. Live public data only. Leads are unverified, and '
                f'nothing is ever submitted to any agency.</div>', unsafe_allow_html=True)
    run = sb.button("Run live scan", type="primary", width="stretch")
    return opts, llm, run


def run_scan(opts: ScanOptions, llm: str) -> None:
    errors = opts.validate()
    if errors:
        for e in errors:
            st.error(e)
        return
    try:
        settings = opts.to_settings()
        if opts.leie_csv:
            settings.leie = load_leie(str(opts.leie_csv), opts.leie_csv.stat().st_mtime)
    except (OSError, ValueError) as exc:
        st.error(f"Configuration error: {exc}")
        return
    with st.status("Running live scan…", expanded=True) as status:
        bar = st.progress(0.0)
        line = st.empty()

        def progress(msg: str, frac: float) -> None:
            bar.progress(min(1.0, frac))
            line.markdown(f'<span class="sc-notice">{esc(msg)}</span>', unsafe_allow_html=True)

        pipeline = Pipeline(settings, backend=make_backend(llm), progress=progress)
        try:
            result = pipeline.run()
        except Exception as exc:  # noqa: BLE001 - surface any failure to the operator
            status.update(label="Scan failed", state="error")
            st.exception(exc)
            return
        finally:
            pipeline.close()
        status.update(label=f"Scan complete: {len(result.results)} recipients screened", state="complete", expanded=False)
    st.session_state["result"] = result
    st.session_state.pop("selected", None)


# --------------------------------------------------------------------------- #
# Inspector (shared by expanding lead cards and the Lead Inspector view)
# --------------------------------------------------------------------------- #


def _ledger_records(result: ScanResult, ids: set[str]) -> list[SourceRecord]:
    if not result.ledger_path.exists():
        return []
    out = []
    for line in result.ledger_path.read_text(encoding="utf-8").splitlines():
        rec = json.loads(line)
        if rec["record_id"] in ids:
            out.append(SourceRecord.model_validate(rec))
    return out


def dossier_for(result: ScanResult, r: EntityResult):
    """The written dossier for leads; an in-memory preview (no evidence archive) for everyone else."""
    if r.dossier:
        return r.dossier
    synth = Synthesizer(high_priority_threshold=result.scan.threshold)
    ids = source_record_ids(r.entity, r.enrichment, r.score)
    return synth.build_dossier(r.entity, r.enrichment, r.score, result.context, _ledger_records(result, ids), result.scan, [])


def export_buttons(result: ScanResult, r: EntityResult, key: str) -> None:
    if r.dossier and r.md_path and r.json_path and r.evidence_path:
        d = r.dossier
        c1, c2, c3, c4 = st.columns(4)
        c1.download_button("Markdown briefing", r.md_path.read_bytes(), file_name=f"{d.dossier_id}.md", mime="text/markdown",
                           key=f"{key}-md", width="stretch")
        c2.download_button("JSON dossier", r.json_path.read_bytes(), file_name=f"{d.dossier_id}.json", mime="application/json",
                           key=f"{key}-json", width="stretch")
        c3.download_button("Provenance (JSON)", (r.evidence_path / "provenance.json").read_bytes(),
                           file_name=f"{d.dossier_id}_provenance.json", mime="application/json", key=f"{key}-prov", width="stretch")
        c4.download_button("Evidence archive (.zip)", zip_dir(r.evidence_path), file_name=f"{d.dossier_id}_evidence.zip",
                           mime="application/zip", key=f"{key}-zip", width="stretch")
        st.caption(f"Archive root hash (SHA-256 of SHA256SUMS): `{r.evidence_root_sha256}`")
    else:
        preview = Synthesizer(high_priority_threshold=result.scan.threshold).render_markdown(dossier_for(result, r))
        st.download_button("Briefing preview (below threshold, no evidence archive)", preview.encode(),
                           file_name=f"preview_{r.entity.entity_key.replace(':', '_')}.md", mime="text/markdown", key=f"{key}-preview")


def inspector_panel(result: ScanResult, r: EntityResult, key: str, full: bool = False) -> None:
    e, enr, s = r.entity, r.enrichment, r.score
    if full:
        c1, c2 = st.columns([1.15, 1])
        with c1:
            lead_card(r, 76)
            npi = enr.npi.best_match
            sf = enr.strike_force
            kv = [("UEI / CAGE", f"{e.uei or 'n/a'} / {e.cage or 'n/a'}"),
                  ("Organizational NPI", f"{npi.npi} ({npi.status})" if npi else ("none matched" if enr.npi.performed else "not checked")),
                  ("Total awarded", f"${e.total_awarded:,.2f} · {len(e.awards)} award(s)"),
                  ("Strike Force district", (sf.district or "outside preset districts") if sf else "no preset"),
                  ("County / tract", f"{enr.geocode.county_name or 'n/a'} / {enr.geocode.census_tract or 'n/a'}"),
                  ("Formation date", f"{e.formation_date or 'unknown'}"),
                  ("People on record", "; ".join(o.name for o in e.officers) or "none published"),
                  ("Land use (OSM)", enr.land_use.classification.value),
                  ("LEIE", f"{len(enr.leie.matches)} candidate(s)" if enr.leie.performed else "not screened"),
                  ("NPPES lookup", " → ".join(enr.npi.stages) or "performed" if enr.npi.performed else "not applicable"),
                  ("Data gaps", "; ".join(e.data_gaps) or "none")]
            card('<div class="sc-section">Identity</div><div class="sc-kv">' + "".join(f"<div>{esc(k)}</div><div>{esc(v)}</div>" for k, v in kv) + "</div>")
        with c2:
            if enr.geocode.matched and enr.geocode.latitude is not None:
                import pandas as pd

                st.map(pd.DataFrame({"lat": [enr.geocode.latitude], "lon": [enr.geocode.longitude]}), zoom=15, size=18, height=300)
                st.caption(f"Census match: {enr.geocode.matched_address} ({enr.geocode.latitude:.5f}, {enr.geocode.longitude:.5f})")
            else:
                empty_state("⌖", "Address not geocoded", enr.geocode.error or "The Census Geocoder returned no match for the address of record.")

    st.markdown('<div class="sc-section">Deterministic score breakdown</div>', unsafe_allow_html=True)
    st.dataframe([{"Rule": ev.rule_id, "Indicator": ev.title, "Status": ev.status.value.replace("_", " "), "Weight": ev.weight,
                   "Points": ev.points_awarded, "Rationale": ev.rationale} for ev in s.evaluations],
                 width="stretch", hide_index=True, key=f"{key}-rules")
    st.caption(f"`{s.formula}`")

    cap = enr.capacity
    st.markdown('<div class="sc-section">Mathematical breakdown</div>', unsafe_allow_html=True)
    if cap.performed:
        m1, m2, m3 = st.columns(3)
        m1.metric("Implied daily beneficiaries", f"{cap.estimated_daily_beneficiaries:,.1f}")
        m2.metric("Licensed capacity" if cap.capacity_basis == "state_license" else "Facility-type ceiling", f"{cap.licensed_capacity}")
        m3.metric("Ratio", f"{cap.capacity_ratio:.2f}x", delta=f"threshold {cap.ratio_threshold}x", delta_color="off")
        st.code(cap.formula, language=None)
        st.caption(f"{cap.benchmark_label}. {cap.benchmark_citation}")
    else:
        st.caption(cap.reason_not_performed or "Not computed.")

    d = dossier_for(result, r)
    with st.expander("Raw API payloads (chain of custody)"):
        for rec in d.sources:
            st.markdown(f"**`{rec.record_id}`** · {rec.source} · `{rec.method} {rec.url}` · {rec.retrieved_at:%Y-%m-%d %H:%M:%S} UTC · "
                        f"SHA-256 `{(rec.response_sha256 or '')[:16]}…`")
            st.json(rec.raw_snippet if rec.raw_snippet is not None else {}, expanded=False)

    if full:
        with st.expander("Physical site verification"):
            for sig in enr.land_use.signals:
                st.write(f"- {sig}")
            if enr.vision.assessment and enr.vision.imagery:
                a, im = enr.vision.assessment, enr.vision.imagery
                st.write(f"**Vision ({enr.vision.model})**: `{a.category.value}`, confidence {a.confidence:.2f}. "
                         f"Markers: {', '.join(a.physical_markers) or 'none'}")
                st.caption(f"{im.provider} {im.image_id} · captured {im.captured_at} · {im.license_note}")
            else:
                st.caption(f"Vision check: {enr.vision.error or 'not run'}")
        with st.expander("Network: shared addresses and officers"):
            summary = result.context.graph.summary(e.entity_key) if result.context else None
            if summary and (len(summary.component_entities) > 1 or summary.shared_people):
                st.graphviz_chart(summary.dot or "graph {}", width="stretch")
                for sa in summary.shared_addresses:
                    st.write(f"**Shared address** {sa['address']}: {'; '.join(sa['entities'])}")
                for sp in summary.shared_people:
                    st.write(f"**Shared person** {sp['person']}: {'; '.join(sp['entities'])}")
            else:
                st.caption("This recipient shares no address or officer with another awardee in the scan.")
        with st.expander("2-minute physical verification checklist", expanded=True):
            from scrimmy.synthesizer import QUICK_PHYSICAL_CHECKLIST

            for i, item in enumerate(QUICK_PHYSICAL_CHECKLIST):
                st.checkbox(item, key=f"{key}-quick-{i}")
        with st.expander("Full ground-truthing checklist"):
            for i, item in enumerate(d.ground_truthing_checklist):
                st.checkbox(item, key=f"{key}-chk-{i}")
            st.caption("Follow docs/FIELD_INVESTIGATOR_SOP.md. Public records and public right-of-way observation only.")

    st.markdown('<div class="sc-section">Export</div>', unsafe_allow_html=True)
    export_buttons(result, r, key)


# --------------------------------------------------------------------------- #
# Views
# --------------------------------------------------------------------------- #


def metrics(result: ScanResult) -> None:
    top = max((r.score.score for r in result.results), default=0)
    cols = st.columns(4)
    values = [("Awards scanned", f"{result.awards_scanned:,}"), ("Recipients screened", f"{len(result.results):,}"),
              ("Anomalies flagged", f"{len(result.leads):,}"), ("Highest discrepancy score", f"{top}")]
    for col, (label, value) in zip(cols, values, strict=True):
        with col:
            card(f'<div class="sc-metric-label">{label}</div><div class="sc-metric-value">{value}</div>')


def _goto_inspector(entity_key: str) -> None:
    # Runs as a callback (before the rerun), which is when Streamlit allows widget state to be set.
    st.session_state["selected"] = entity_key
    st.session_state["view_ctl"] = "Lead Inspector"


def view_scanner(result: ScanResult) -> None:
    metrics(result)
    if result.failed_groups:
        st.warning("Some USASpending searches failed, so results are incomplete: "
                   + "; ".join(f"{g}: {e}" for g, e in result.failed_groups.items()))
    partial = [r for r in result.results if r.entity.data_gaps]
    if partial:
        st.info(f"{len(partial)} recipient(s) had partial lookups and were scored on the data available; affected rules show "
                "'insufficient data' (see Data gaps in the Lead Inspector).")
    if result.skipped:
        st.info(f"{len(result.skipped)} recipient(s) had no award with a positive amount and were not scored.")
    if not result.results:
        if result.failed_groups:
            empty_state("⚠︎", "Live data unavailable", "Every USASpending request failed. Check network access to api.usaspending.gov.")
        else:
            empty_state("◎", "No awards matched these filters", "Try another preset, fiscal year or a lower award floor.")
        return
    show_all = st.toggle("Show recipients below threshold", value=False)
    rows = result.results if show_all else result.leads
    if not rows:
        empty_state("✓", "No anomalies found matching current threshold",
                    f"{len(result.results)} recipients were screened against live records. None scored {result.scan.threshold} or higher.")
        return
    st.markdown(f'<div class="sc-section">{"All recipients" if show_all else "Anomalies flagged"}</div>', unsafe_allow_html=True)
    for r in rows:
        lead_card(r)
        with st.expander(f"Inspect {r.entity.name}"):
            inspector_panel(result, r, key=f"scan-{r.entity.entity_key}")
            st.button("Open in Lead Inspector", key=f"open-{r.entity.entity_key}", on_click=_goto_inspector, args=(r.entity.entity_key,))


def view_inspector(result: ScanResult) -> None:
    if not result.results:
        empty_state("◎", "Nothing to inspect", "The scan returned no recipients.")
        return
    keys = [r.entity.entity_key for r in result.results]
    by_key = {r.entity.entity_key: r for r in result.results}
    current = st.session_state.get("selected")
    choice = st.selectbox("Recipient", keys, index=keys.index(current) if current in keys else 0,
                          format_func=lambda k: f"{by_key[k].score.score:>3} · {by_key[k].entity.name}")
    st.session_state["selected"] = choice
    inspector_panel(result, by_key[choice], key=f"insp-{choice}", full=True)


def _cell(r: EntityResult, rule_id: str) -> str:
    ev = next(ev for ev in r.score.evaluations if ev.rule_id == rule_id)
    if ev.status is RuleStatus.TRIGGERED:
        return f'<span class="sc-cell {"hit" if ev.points_awarded else "hit-0"}" title="{esc(ev.rationale)}">+{ev.points_awarded}</span>'
    symbol, cls = {RuleStatus.NOT_TRIGGERED: ("·", "no"), RuleStatus.INSUFFICIENT_DATA: ("?", "insuf"),
                   RuleStatus.NOT_APPLICABLE: ("–", "na")}[ev.status]
    return f'<span class="sc-cell {cls}" title="{esc(ev.rationale)}">{symbol}</span>'


def view_matrix(result: ScanResult) -> None:
    if not result.results:
        empty_state("◎", "No recipients to compare", "Run a live scan with broader filters.")
        return
    only_leads = st.toggle("Anomalies only", value=False)
    rows = result.leads if only_leads else result.results
    if not rows:
        empty_state("✓", "No anomalies found matching current threshold", "Turn off 'Anomalies only' to see every screened recipient.")
        return
    head = "".join(f"<th>{esc(MATRIX_COLS[rid])}</th>" for rid in ALL_RULES)
    body = []
    for r in rows:
        sf = r.enrichment.strike_force
        body.append(f'<tr><td><b>{esc(r.entity.name)}</b><div class="sc-district">{esc(sf.district if sf and sf.district else r.entity.address.one_line)}</div></td>'
                    f'<td>{money(r.entity.total_awarded)}</td>' + "".join(f"<td>{_cell(r, rid)}</td>" for rid in ALL_RULES)
                    + f'<td class="sc-score">{r.score.score}</td></tr>')
    card(f'<div class="sc-scroll"><table class="sc-matrix"><thead><tr><th>Recipient</th><th>Awarded</th>{head}<th>Score</th></tr></thead>'
         f'<tbody>{"".join(body)}</tbody></table></div>'
         '<div class="sc-legend"><span><b>+n</b> triggered (points)</span><span><b>·</b> not triggered</span>'
         '<span><b>?</b> insufficient data</span><span><b>–</b> not applicable</span><span>Hover a cell for the rationale.</span></div>')


def view_export(result: ScanResult) -> None:
    if not result.leads:
        empty_state("✓", "No anomalies found matching current threshold", "Only flagged leads are exported with evidence archives.")
        return
    for r in result.leads:
        lead_card(r)
        export_buttons(result, r, key=f"exp-{r.entity.entity_key}")
    st.markdown('<div class="sc-notice">Each archive contains the raw API responses, ledger, provenance.json, the dossier and a '
                'SHA256SUMS file (<code>sha256sum -c SHA256SUMS</code>). Exports are working papers for intake agents and '
                'whistleblower counsel. Verify every indicator before any filing.</div>', unsafe_allow_html=True)


# --------------------------------------------------------------------------- #
# Main
# --------------------------------------------------------------------------- #


def main() -> None:
    st.markdown(CSS, unsafe_allow_html=True)
    st.markdown(EXTRA_CSS, unsafe_allow_html=True)
    opts, llm, run = sidebar()
    st.markdown('<div class="sc-title">Forensic Workstation</div><div class="sc-sub">Live screening of federal award recipients in '
                'DOJ Health Care Fraud Strike Force regions against USASpending, NPPES, Census, OpenStreetMap and HHS-OIG records. '
                'Scores are deterministic. Every lead is unverified until a person checks it.</div>', unsafe_allow_html=True)
    if run:
        run_scan(opts, llm)
    if "view_ctl" not in st.session_state:
        st.session_state["view_ctl"] = VIEWS[0]
    view = st.segmented_control("View", VIEWS, label_visibility="collapsed", key="view_ctl") or VIEWS[0]
    result: ScanResult | None = st.session_state.get("result")
    if result is None:
        empty_state("◎", "Ready to scan", "Pick a Strike Force preset, verticals and an award floor in the sidebar, then run a live scan.")
        return
    {"Strike Force Scanner": view_scanner, "Lead Inspector": view_inspector, "Anomaly Matrix": view_matrix,
     "Dossier Export": view_export}[view](result)


main()
