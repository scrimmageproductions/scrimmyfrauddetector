"""Scrimmy Forensic Console: a Streamlit front end for live scans.

    streamlit run streamlit_app/app.py

For Streamlit Community Cloud, Render, Railway, Fly.io or any host that runs a long-lived
process. Vercel cannot run Streamlit (it needs a persistent WebSocket); the Vercel
deployment uses public/ + api/index.py instead.

Every scan makes live HTTP calls to USASpending.gov, CMS NPPES, the U.S. Census
Geocoder and OpenStreetMap Overpass (plus HHS-OIG LEIE / state licensing / imagery
when configured). The app contains no sample, cached or fabricated records. If a
scan returns nothing, it shows an empty state; if the APIs are unreachable, it shows
the error.
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

from scrimmy import __version__
from scrimmy.clients import EvidenceLedger, ResilientHTTP, SourceAPIError
from scrimmy.leie_matcher import LEIE_DOWNLOAD_URL, LEIEMatcher
from scrimmy.models import RuleStatus, SourceRecord
from scrimmy.options import ScanOptions
from scrimmy.pipeline import Pipeline, ScanResult
from scrimmy.scorer import (
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
from scrimmy.synthesizer import Synthesizer, make_backend
from scrimmy.verticals import PRIORITY_VERTICALS, VERTICALS

st.set_page_config(page_title="Scrimmy Forensic Console", page_icon="◎", layout="wide", initial_sidebar_state="expanded")

DATA_DIR = Path(os.environ.get("SCRIMMY_DATA_DIR", "./data"))
OUTPUT_ROOT = Path(os.environ.get("SCRIMMY_OUTPUT_DIR", "./dossiers"))
LEIE_PATH = DATA_DIR / "LEIE_UPDATED.csv"

STATES = ["AL", "AK", "AZ", "AR", "CA", "CO", "CT", "DE", "DC", "FL", "GA", "HI", "ID", "IL", "IN", "IA", "KS", "KY", "LA",
          "ME", "MD", "MA", "MI", "MN", "MS", "MO", "MT", "NE", "NV", "NH", "NJ", "NM", "NY", "NC", "ND", "OH", "OK", "OR",
          "PA", "RI", "SC", "SD", "TN", "TX", "UT", "VT", "VA", "WA", "WV", "WI", "WY", "PR", "GU", "VI", "AS", "MP"]
AWARD_FLOORS = {"$100K": 100_000, "$250K": 250_000, "$500K": 500_000, "$1M+": 1_000_000}

# Status pills: Apple system colors (light / dark variants handled in CSS).
PILLS = {
    MATHEMATICAL_IMPOSSIBILITY: ("Capacity Variance", "red"),
    LEIE_EXCLUSION_MATCH: ("LEIE Match", "red"),
    PHANTOM_HEALTHCARE_PROVIDER: ("No Active NPI", "orange"),
    HIGH_DOLLAR_RESIDENTIAL_ZONE: ("Residential / Mail Drop", "orange"),
    PHYSICAL_SITE_MISMATCH: ("Site Mismatch", "orange"),
    NEWLY_FORMED_WINDFALL: ("Newly Formed", "yellow"),
    CO_LOCATED_SHELL_CLUSTER: ("Co-location", "purple"),
    EXECUTIVE_SERIAL_REGISTRATION: ("Serial Officer", "indigo"),
    UNRESOLVED_PHYSICAL_DISCREPANCY: ("Unresolved Address", "gray"),
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


def pills(result) -> str:
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
# Sidebar: targeting matrix
# --------------------------------------------------------------------------- #


def sidebar() -> tuple[ScanOptions, str, bool]:
    sb = st.sidebar
    sb.markdown('<div class="sc-section">Targeting</div>', unsafe_allow_html=True)
    verticals = sb.pills("Verticals", PRIORITY_VERTICALS, default=["childcare"], selection_mode="multi",
                         format_func=lambda k: VERTICALS[k].label.split(" and ")[0].replace("Specialized ", "").title())
    state = sb.selectbox("State", STATES, index=STATES.index("MN"))
    floor = sb.select_slider("Minimum award", list(AWARD_FLOORS), value="$250K")
    this_fy = date.today().year + (1 if date.today().month >= 10 else 0)
    fy = sb.selectbox("Fiscal year", ["All years", *range(this_fy, 2007, -1)], index=1)
    max_awards = sb.slider("Award rows to pull", 25, 500, 150, step=25)

    sb.markdown('<div class="sc-section">Scoring</div>', unsafe_allow_html=True)
    profile = sb.segmented_control("Profile", ["registry", "capacity"], default="registry",
                                   format_func=lambda p: "Registry" if p == "registry" else "Capacity")
    threshold = sb.slider("High-priority threshold", 0, 100, 50, step=5)

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
                                help="See config/license_sources.example.json. Needed for capacity arithmetic.")
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
        state=state,
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
    sb.markdown(f'<div class="sc-notice">scrimmy {__version__}. Live public data only. Leads are unverified and '
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
    st.session_state["scan_opts"] = opts
    st.session_state.pop("selected", None)


# --------------------------------------------------------------------------- #
# Views
# --------------------------------------------------------------------------- #


def metrics(result: ScanResult) -> None:
    top = max((r.score.score for r in result.results), default=0)
    cols = st.columns(4)
    values = [("Live awards scanned", f"{result.awards_scanned:,}"), ("Recipients screened", f"{len(result.results):,}"),
              ("High-priority leads", f"{len(result.leads):,}"), ("Highest score", f"{top}")]
    for col, (label, value) in zip(cols, values, strict=True):
        with col:
            card(f'<div class="sc-metric-label">{label}</div><div class="sc-metric-value">{value}</div>')


def view_scanner(result: ScanResult) -> None:
    metrics(result)
    if result.failed_groups:
        st.warning("Some USASpending searches failed, so results are incomplete: "
                   + "; ".join(f"{g}: {e}" for g, e in result.failed_groups.items()))
    if not result.results:
        if result.failed_groups:
            empty_state("⚠︎", "Live data unavailable", "Every USASpending request failed. Check network access to api.usaspending.gov.")
        else:
            empty_state("◎", "No awards matched these filters", "Try another state, fiscal year or a lower award floor.")
        return
    leads = result.leads
    show_all = st.toggle("Show recipients below threshold", value=False)
    rows = result.results if show_all else leads
    if not rows:
        empty_state("✓", "No anomalies found matching current threshold",
                    f"{len(result.results)} recipients were screened against live records. None scored {result.scan.threshold} or higher.")
        return
    st.markdown(f'<div class="sc-section">{"All recipients" if show_all else "High-priority forensic leads"}</div>', unsafe_allow_html=True)
    for r in rows:
        e = r.entity
        card(f'<div class="sc-lead">{ring(r.score.score)}<div class="sc-lead-main"><div class="sc-lead-name">{esc(e.name)}</div>'
             f'<div class="sc-lead-meta">{esc(e.address.one_line or "No address published")} · UEI {esc(e.uei or "n/a")} · '
             f'{len(e.awards)} award{"s" if len(e.awards) != 1 else ""}</div><div>{pills(r)}</div></div>'
             f'<div><div class="sc-amount">{money(e.total_awarded)}</div><div class="sc-amount-sub">awarded in scope</div></div></div>')
        st.button("Inspect", key=f"inspect-{e.entity_key}", on_click=_goto_inspector, args=(e.entity_key,))


def _goto_inspector(entity_key: str) -> None:
    # Runs as a callback (before the rerun), which is when Streamlit allows widget state to be set.
    st.session_state["selected"] = entity_key
    st.session_state["view_ctl"] = "Target Inspector"


def _selected(result: ScanResult):
    keys = [r.entity.entity_key for r in result.results]
    if not keys:
        return None
    current = st.session_state.get("selected")
    idx = keys.index(current) if current in keys else 0
    by_key = {r.entity.entity_key: r for r in result.results}
    choice = st.selectbox("Recipient", keys, index=idx,
                          format_func=lambda k: f"{by_key[k].score.score:>3} · {by_key[k].entity.name}")
    st.session_state["selected"] = choice
    return by_key[choice]


def _dossier_for(result: ScanResult, r):
    if r.dossier:
        return r.dossier
    synth = Synthesizer(high_priority_threshold=result.scan.threshold)
    ids = Pipeline.source_ids(r.entity, r.enrichment, r.score)
    ledger_records = []
    if result.ledger_path.exists():
        for line in result.ledger_path.read_text(encoding="utf-8").splitlines():
            rec = json.loads(line)
            if rec["record_id"] in ids:
                ledger_records.append(rec)
    return synth.build_dossier(r.entity, r.enrichment, r.score, result.context, [SourceRecord.model_validate(x) for x in ledger_records],
                               result.scan, [])


def view_inspector(result: ScanResult) -> None:
    r = _selected(result)
    if r is None:
        empty_state("◎", "Nothing to inspect", "Run a live scan first.")
        return
    e, enr, s = r.entity, r.enrichment, r.score
    c1, c2 = st.columns([1.15, 1])
    with c1:
        card(f'<div class="sc-lead">{ring(s.score, 76)}<div class="sc-lead-main"><div class="sc-lead-name" style="font-size:21px">{esc(e.name)}</div>'
             f'<div class="sc-lead-meta">{esc(e.address.one_line)}</div><div>{pills(r)}</div></div></div>')
        kv = [("UEI / CAGE", f"{e.uei or 'n/a'} / {e.cage or 'n/a'}"),
              ("Organizational NPI", f"{enr.npi.best_match.npi} ({enr.npi.best_match.status})" if enr.npi.best_match else ("none matched" if enr.npi.performed else "not checked")),
              ("Total awarded", f"${e.total_awarded:,.2f} · {len(e.awards)} award(s)"),
              ("Formation date", f"{e.formation_date or 'unknown'}"),
              ("People on record", "; ".join(o.name for o in e.officers) or "none published"),
              ("Land use (OSM)", enr.land_use.classification.value),
              ("LEIE", f"{len(enr.leie.matches)} candidate(s)" if enr.leie.performed else "not screened")]
        card('<div class="sc-section">Identity</div><div class="sc-kv">' + "".join(f"<div>{esc(k)}</div><div>{esc(v)}</div>" for k, v in kv) + "</div>")
    with c2:
        if enr.geocode.matched and enr.geocode.latitude is not None:
            import pandas as pd

            st.map(pd.DataFrame({"lat": [enr.geocode.latitude], "lon": [enr.geocode.longitude]}), zoom=15, size=18, height=270)
            st.caption(f"Census match: {enr.geocode.matched_address} ({enr.geocode.latitude:.5f}, {enr.geocode.longitude:.5f})")
        else:
            empty_state("⌖", "Address not geocoded", enr.geocode.error or "The Census Geocoder returned no match for the address of record.")

    st.markdown('<div class="sc-section">Deterministic score breakdown</div>', unsafe_allow_html=True)
    st.dataframe([{"Rule": ev.rule_id, "Indicator": ev.title, "Status": ev.status.value.replace("_", " "), "Weight": ev.weight,
                   "Points": ev.points_awarded, "Rationale": ev.rationale} for ev in s.evaluations],
                 width="stretch", hide_index=True)
    st.caption(f"`{s.formula}`")

    cap = enr.capacity
    with st.expander("Disbursement-to-capacity arithmetic", expanded=cap.performed):
        if cap.performed:
            m1, m2, m3 = st.columns(3)
            m1.metric("Implied daily beneficiaries", f"{cap.estimated_daily_beneficiaries:,.1f}")
            m2.metric("Licensed capacity", f"{cap.licensed_capacity}")
            m3.metric("Ratio", f"{cap.capacity_ratio:.2f}x", delta=f"threshold {cap.ratio_threshold}x", delta_color="off")
            st.code(cap.formula, language=None)
            st.caption(f"{cap.benchmark_label}. {cap.benchmark_citation}")
        else:
            st.write(cap.reason_not_performed or "Not computed.")

    with st.expander("Physical site verification"):
        for sig in enr.land_use.signals:
            st.write(f"- {sig}")
        if enr.vision.assessment and enr.vision.imagery:
            a, im = enr.vision.assessment, enr.vision.imagery
            st.write(f"**Vision ({enr.vision.model})**: `{a.category.value}`, confidence {a.confidence:.2f}. Markers: {', '.join(a.physical_markers) or 'none'}")
            st.caption(f"{im.provider} {im.image_id} · captured {im.captured_at} · {im.license_note}")
        else:
            st.caption(f"Vision check: {enr.vision.error or 'not run'}")

    d = _dossier_for(result, r)
    with st.expander("Raw API payloads (chain of custody)"):
        for rec in d.sources:
            st.markdown(f"**`{rec.record_id}`** · {rec.source} · `{rec.method} {rec.url}` · {rec.retrieved_at:%Y-%m-%d %H:%M:%S} UTC · "
                        f"SHA-256 `{(rec.response_sha256 or '')[:16]}…`")
            st.json(rec.raw_snippet if rec.raw_snippet is not None else {}, expanded=False)

    with st.expander("Human verification checklist", expanded=True):
        for i, item in enumerate(d.ground_truthing_checklist):
            st.checkbox(item, key=f"chk-{e.entity_key}-{i}")
        st.caption("Work through docs/FIELD_INVESTIGATOR_SOP.md. Public records and public right-of-way observation only.")


def view_network(result: ScanResult) -> None:
    if not result.context or not result.results:
        empty_state("◎", "No network to show", "Run a live scan first.")
        return
    r = _selected(result)
    summary = result.context.graph.summary(r.entity.entity_key)
    m1, m2, m3 = st.columns(3)
    m1.metric("Entities in component", len(summary.component_entities))
    m2.metric("Shared addresses", len(summary.shared_addresses))
    m3.metric("Shared people", len(summary.shared_people))
    if len(summary.component_entities) <= 1 and not summary.shared_people:
        empty_state("◌", "No shared addresses or people", "This recipient is not linked to any other awardee in the scan.")
    st.graphviz_chart(summary.dot or "graph {}", width="stretch")
    for sa in summary.shared_addresses:
        st.write(f"**Shared address** {sa['address']}: {'; '.join(sa['entities'])}")
    for sp in summary.shared_people:
        st.write(f"**Shared person** {sp['person']}: {'; '.join(sp['entities'])}")


def view_export(result: ScanResult) -> None:
    if not result.leads:
        empty_state("✓", "No anomalies found matching current threshold", "Only High-Priority Forensic Leads are exported with evidence archives.")
        return
    for r in result.leads:
        d = r.dossier
        card(f'<div class="sc-lead">{ring(r.score.score)}<div class="sc-lead-main"><div class="sc-lead-name">{esc(r.entity.name)}</div>'
             f'<div class="sc-lead-meta">{esc(d.dossier_id)} · suggested routing: {esc(d.track.primary if d.track else "n/a")} · '
             f'{len(d.evidence_manifest)} archived artifacts</div></div></div>')
        c1, c2, c3 = st.columns(3)
        c1.download_button("Markdown briefing", r.md_path.read_bytes(), file_name=f"{d.dossier_id}.md", mime="text/markdown",
                           key=f"md-{d.dossier_id}", width="stretch")
        c2.download_button("JSON provenance", r.json_path.read_bytes(), file_name=f"{d.dossier_id}.json", mime="application/json",
                           key=f"json-{d.dossier_id}", width="stretch")
        c3.download_button("Evidence archive (.zip)", zip_dir(r.evidence_path), file_name=f"{d.dossier_id}_evidence.zip",
                           mime="application/zip", key=f"zip-{d.dossier_id}", width="stretch")
    st.markdown('<div class="sc-notice">Each archive contains the raw API responses, ledger, manifest, dossier and a SHA256SUMS file '
                '(<code>sha256sum -c SHA256SUMS</code>). Exports are working papers for counsel and investigators. Verify every '
                'indicator before any filing.</div>', unsafe_allow_html=True)


# --------------------------------------------------------------------------- #
# Main
# --------------------------------------------------------------------------- #


def main() -> None:
    st.markdown(CSS, unsafe_allow_html=True)
    opts, llm, run = sidebar()
    st.markdown('<div class="sc-title">Forensic Console</div><div class="sc-sub">Live screening of federal award recipients against '
                'USASpending, NPPES, Census, OpenStreetMap and HHS-OIG records. Scores are deterministic. Every lead is unverified '
                'until a person checks it.</div>', unsafe_allow_html=True)
    if run:
        run_scan(opts, llm)
    views = ["Live Scanner", "Target Inspector", "Network Map", "Dossier Export"]
    if "view_ctl" not in st.session_state:
        st.session_state["view_ctl"] = views[0]
    view = st.segmented_control("View", views, label_visibility="collapsed", key="view_ctl")
    st.session_state["view"] = view or views[0]
    result: ScanResult | None = st.session_state.get("result")
    if result is None:
        empty_state("◎", "Ready to scan", "Choose verticals, a state and an award floor in the sidebar, then run a live scan.")
        return
    {"Live Scanner": view_scanner, "Target Inspector": view_inspector, "Network Map": view_network,
     "Dossier Export": view_export}[st.session_state["view"]](result)


main()
