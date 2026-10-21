"""Stage 4 - Synthesis & Dossier Generation.

Division of labour:

* Everything numeric or evidentiary (score, flags, evidence matrix, sources,
  statutes, checklist) is rendered deterministically from the data models.
* An optional LLM writes *only* the Executive Summary prose, from a closed
  fact sheet. Its output must pass ``LanguageGuard`` (no accusatory language,
  no numbers absent from the fact sheet). On any failure the deterministic
  template is used instead, and the fallback is recorded in the dossier.

Backends: ``template`` (default, offline), ``anthropic``, ``openai``,
``ollama`` (local HTTP), ``litellm`` (any provider LiteLLM supports).
"""

from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

import httpx

from .models import (
    AwardeeEntity,
    EnrichmentData,
    FraudRiskDossier,
    LandUseClass,
    NarrativeProvenance,
    RuleStatus,
    ScanContext,
    ScoreBreakdown,
    SourceRecord,
    utcnow,
)
from .scorer import (
    RULE_COLOCATION,
    RULE_EXEC_DUP,
    RULE_NEWLY_FORMED,
    RULE_PHANTOM_HEALTH,
    RULE_RESIDENTIAL,
    ScoringContext,
)

log = logging.getLogger(__name__)

DISCLAIMER = (
    "This dossier is an automated investigative LEAD generated from public records for review by a qualified human "
    "investigator. It is not a finding, allegation or accusation of wrongdoing by any person or entity. Every indicator "
    "below describes an unverified discrepancy in public data that may have a lawful explanation (data-entry error, "
    "stale registry records, legitimate home-based operations, shared professional office space, affiliated entities "
    "not linked in public data, etc.). No report has been submitted to any agency by this tool. Do not publish, "
    "circulate or act on this document without independent verification."
)

# --------------------------------------------------------------------------- #
# Statutes and checklists (deterministic content)
# --------------------------------------------------------------------------- #

STATUTES: dict[str, dict[str, str]] = {
    "FCA": {"citation": "31 U.S.C. §§ 3729-3733", "name": "False Claims Act",
            "relevance": "Civil liability for knowingly presenting false claims for federal funds; § 3730(b) qui tam "
                         "provisions and § 3730(h) anti-retaliation protections for whistleblowers."},
    "FCA_PDB": {"citation": "31 U.S.C. § 3730(e)(4)", "name": "False Claims Act - public disclosure bar",
                "relevance": "Qui tam actions based on publicly disclosed information (including federal reports and "
                             "databases such as USASpending) may be barred unless the relator is an 'original source' "
                             "with independent knowledge. Consult counsel."},
    "MAIL": {"citation": "18 U.S.C. § 1341", "name": "Mail fraud",
             "relevance": "Federal offense involving use of the mails in a scheme to obtain money by false pretenses."},
    "WIRE": {"citation": "18 U.S.C. § 1343", "name": "Wire fraud",
             "relevance": "Federal offense involving interstate wire communications (including electronic payment "
                          "and grant-drawdown systems) in a scheme to obtain money by false pretenses."},
    "FALSE_STMT": {"citation": "18 U.S.C. § 1001", "name": "False statements",
                   "relevance": "Materially false statements in matters within federal jurisdiction (e.g. SAM.gov "
                                "registrations, grant applications, certifications)."},
    "PROGRAM_THEFT": {"citation": "18 U.S.C. § 666", "name": "Theft or bribery concerning programs receiving federal funds",
                      "relevance": "Applies to organizations receiving more than $10,000 in federal program benefits in a one-year period."},
    "CONSPIRACY": {"citation": "18 U.S.C. § 371", "name": "Conspiracy to defraud the United States",
                   "relevance": "Relevant where multiple coordinated entities or individuals are involved."},
    "HC_FRAUD": {"citation": "18 U.S.C. § 1347", "name": "Health care fraud",
                 "relevance": "Schemes to defraud any health care benefit program, including Medicare and Medicaid."},
    "AKS": {"citation": "42 U.S.C. § 1320a-7b(b)", "name": "Anti-Kickback Statute",
            "relevance": "Remuneration for referrals of federal health care program business."},
    "CMPL": {"citation": "42 U.S.C. § 1320a-7a", "name": "Civil Monetary Penalties Law",
             "relevance": "Administrative penalties for false or fraudulent claims to federal health care programs."},
    "PFCRA": {"citation": "31 U.S.C. §§ 3801-3812", "name": "Program Fraud Civil Remedies Act",
              "relevance": "Administrative remedies for false claims and statements of $150,000 or less."},
}

RULE_STATUTES: dict[str, list[str]] = {
    RULE_NEWLY_FORMED: ["FALSE_STMT", "PROGRAM_THEFT"],
    RULE_RESIDENTIAL: ["FALSE_STMT", "WIRE", "MAIL"],
    RULE_PHANTOM_HEALTH: ["HC_FRAUD", "CMPL"],
    RULE_COLOCATION: ["CONSPIRACY", "FALSE_STMT"],
    RULE_EXEC_DUP: ["CONSPIRACY", "FALSE_STMT"],
}

BASE_CHECKLIST = [
    "Re-run every source URL in the Source Ledger and confirm the responses still match the recorded SHA-256 hashes or note what changed.",
    "Pull the full award files (USASpending award pages, FPDS/FAC audits, agency grant notices) for each award listed and record the stated place of performance and purpose.",
    "Retrieve the entity's Secretary of State filing history (formation date, registered agent, officers, status, annual reports) and save dated copies.",
    "Retrieve the SAM.gov entity registration (status, entity start date, physical vs. mailing address, POCs, exclusions) and check SAM.gov exclusions and HHS-OIG LEIE.",
    "Search county property-tax / assessor records for the address of record: owner of record, property class code (residential/commercial), homestead exemption status.",
    "Check whether the entity holds the operating licenses its awards would require (state child care licensing, health facility licensing, professional licenses) and the licensed capacity at the address.",
    "Search the Federal Audit Clearinghouse for Single Audit reports if the entity expended $1,000,000 or more (FY2025+ threshold; $750,000 previously) in federal awards in a fiscal year.",
    "Document innocent explanations considered and why each was or was not ruled out.",
]

RULE_CHECKLIST: dict[str, list[str]] = {
    RULE_NEWLY_FORMED: [
        "Confirm the formation date directly from the state filing (not a third-party aggregator); check for predecessor entities, mergers or name changes that would explain a short operating history.",
        "Compare the award solicitation/notice date to the formation date; determine whether the entity certified prior experience or past performance in its application.",
    ],
    RULE_RESIDENTIAL: [
        "From a public right-of-way only, photograph the premises with date/time and GPS metadata: signage, business hours, deliveries, client traffic. Do not trespass, enter, or contact occupants.",
        "Check Google Street View / county imagery history for the address to see whether a business presence existed during the award period.",
        "If the address is a CMRA/PMB or P.O. Box, determine the entity's actual place of performance from award documents, licensing records and NPPES practice locations.",
        "Check zoning and home-occupation permits with the municipality; confirm whether the activity funded is permitted at a residence.",
    ],
    RULE_PHANTOM_HEALTH: [
        "Search NPPES manually by name variants, DBA names, prior names and individual practitioners; check whether services were billed under an individual (Type 1) NPI or a parent organization.",
        "Check the state Medicaid provider enrollment directory and CMS Provider Enrollment (PECOS) data where publicly available.",
        "Confirm whether the funded activity actually required NPI enumeration (e.g. grant administration or research does not).",
    ],
    RULE_COLOCATION: [
        "For each co-located entity, pull Secretary of State records and compare registered agents, officers, formation dates and filing patterns.",
        "Determine whether the address is a legitimate multi-tenant office, registered-agent service, accountant's office or business incubator (common lawful explanations).",
    ],
    RULE_EXEC_DUP: [
        "Confirm the individual's identity across entities using full name plus middle name, title, and corporate filings; common names produce false matches.",
        "Determine whether the entities are lawfully affiliated (franchise, management company, common ownership disclosed in filings) and whether that relationship was disclosed in award applications.",
    ],
}

COMMON_CAVEATS = [
    "Scores reflect only the five documented heuristics applied to public data at the time of retrieval; a high score is a prioritisation signal, not evidence of intent or wrongdoing.",
    "Cross-entity rules (co-location, officer overlap) only consider recipients returned by this scan's filters; entities outside the scan are not compared.",
    "OpenStreetMap is volunteer-maintained; missing or outdated building/POI tags are common, especially in rural and newly built areas.",
    "Census Geocoder coordinates are interpolated along street segments and can be offset by tens of meters from the actual parcel.",
    "USASpending recipient addresses reflect the most recent SAM/award data and may not match the address at the time of award.",
    "Person-name matches are on normalized names only and do not confirm that two records refer to the same individual.",
]


# --------------------------------------------------------------------------- #
# Language guard
# --------------------------------------------------------------------------- #


class LanguageGuard:
    """Rejects narrative text that is accusatory or cites numbers not present in the fact sheet."""

    FORBIDDEN = [
        r"\b(is|are|was|were|be|being)\s+(an?\s+|engaged\s+in\s+|committing\s+)?(fraud|fraudulent|scam|scams|criminal|crook|thie(f|ves)|guilty|corrupt)\b",
        r"\bcommitt?(ed|ing)\s+(fraud|a\s+crime|crimes|theft)\b",
        r"\bguilty\b", r"\bembezzl\w*", r"\bst(ole|olen|ealing)\b", r"\bdefraud(ed|ing|s)\b",
        r"\bsham\b", r"\bbogus\b", r"\blaunder\w*", r"\bkickbacks?\b", r"\bcriminals?\b", r"\billegal(ly)?\b",
        r"\bprov(e|es|ed|en)\s+(that\s+)?(the\s+)?(fraud|wrongdoing|guilt|crime)", r"\bshell\s+compan(y|ies)\b",
        r"\bfake\b", r"\bphantom\b", r"\bfraudster", r"\bscheme\s+to\b", r"\bripp(ed|ing)\s+off\b",
    ]
    _NUM_RE = re.compile(r"\d[\d,]*(?:\.\d+)?")

    def __init__(self) -> None:
        self._forbidden = [re.compile(p, re.I) for p in self.FORBIDDEN]

    def violations(self, text: str, facts: Any | None = None) -> list[str]:
        problems = [f"accusatory/unsupported language: '{m.group(0)}'" for rx in self._forbidden for m in rx.finditer(text)]
        if facts is not None:
            allowed = self.allowed_numbers(facts)
            for raw in self._NUM_RE.findall(text):
                n = self._norm(raw)
                if n and n not in allowed and not (n.isdigit() and int(n) <= 10):
                    problems.append(f"number not present in fact sheet: '{raw}'")
        return problems

    @classmethod
    def _norm(cls, raw: str) -> str:
        n = raw.replace(",", "").rstrip(".")
        if "." in n:
            n = n.rstrip("0").rstrip(".")
        return n.lstrip("0") or "0"

    @classmethod
    def allowed_numbers(cls, facts: Any) -> set[str]:
        blob = json.dumps(facts, default=str)
        return {cls._norm(x) for x in cls._NUM_RE.findall(blob)}


# --------------------------------------------------------------------------- #
# LLM backends
# --------------------------------------------------------------------------- #


class LLMBackend(Protocol):
    name: str
    model: str | None

    def complete(self, system: str, prompt: str) -> str: ...


@dataclass
class TemplateBackend:
    name: str = "template"
    model: str | None = None

    def complete(self, system: str, prompt: str) -> str:  # pragma: no cover - never called; template is rendered directly
        raise NotImplementedError


@dataclass
class AnthropicBackend:
    model: str
    name: str = "anthropic"
    max_tokens: int = 900

    def complete(self, system: str, prompt: str) -> str:
        import anthropic  # optional dependency

        client = anthropic.Anthropic()
        resp = client.messages.create(
            model=self.model, max_tokens=self.max_tokens, system=system,
            messages=[{"role": "user", "content": prompt}],
        )
        return "".join(getattr(b, "text", "") for b in resp.content).strip()


@dataclass
class OpenAIBackend:
    model: str
    name: str = "openai"
    base_url: str | None = None

    def complete(self, system: str, prompt: str) -> str:
        import openai  # optional dependency

        client = openai.OpenAI(base_url=self.base_url) if self.base_url else openai.OpenAI()
        resp = client.chat.completions.create(
            model=self.model, temperature=0,
            messages=[{"role": "system", "content": system}, {"role": "user", "content": prompt}],
        )
        return (resp.choices[0].message.content or "").strip()


@dataclass
class OllamaBackend:
    model: str
    name: str = "ollama"
    host: str = "http://localhost:11434"

    def complete(self, system: str, prompt: str) -> str:
        resp = httpx.post(
            f"{self.host.rstrip('/')}/api/chat",
            json={"model": self.model, "stream": False, "options": {"temperature": 0},
                  "messages": [{"role": "system", "content": system}, {"role": "user", "content": prompt}]},
            timeout=180,
        )
        resp.raise_for_status()
        return (resp.json().get("message") or {}).get("content", "").strip()


@dataclass
class LiteLLMBackend:
    model: str
    name: str = "litellm"

    def complete(self, system: str, prompt: str) -> str:
        import litellm  # optional dependency

        resp = litellm.completion(
            model=self.model, temperature=0,
            messages=[{"role": "system", "content": system}, {"role": "user", "content": prompt}],
        )
        return (resp.choices[0].message.content or "").strip()


def make_backend(provider: str = "auto", model: str | None = None) -> LLMBackend:
    """Resolve a backend. 'auto' picks Anthropic/OpenAI if a key is set, else the offline template."""
    provider = (provider or "auto").lower()
    model = model or os.environ.get("SCRIMMY_LLM_MODEL")
    if provider == "auto":
        if os.environ.get("ANTHROPIC_API_KEY"):
            provider = "anthropic"
        elif os.environ.get("OPENAI_API_KEY"):
            provider = "openai"
        else:
            provider = "template"
    if provider == "template":
        return TemplateBackend()
    if provider == "anthropic":
        return AnthropicBackend(model=model or "claude-sonnet-5-5")
    if provider == "openai":
        return OpenAIBackend(model=model or os.environ.get("OPENAI_MODEL", "gpt-4o-mini"), base_url=os.environ.get("OPENAI_BASE_URL"))
    if provider == "ollama":
        return OllamaBackend(model=model or "llama3.1", host=os.environ.get("OLLAMA_HOST", "http://localhost:11434"))
    if provider == "litellm":
        if not model:
            raise ValueError("--llm-model is required for the litellm provider (e.g. 'anthropic/claude-sonnet-5-5').")
        return LiteLLMBackend(model=model)
    raise ValueError(f"Unknown LLM provider: {provider}")


SYSTEM_PROMPT = """You draft the Executive Summary of an investigative lead briefing for a human forensic reviewer.
Hard rules:
1. Use ONLY the facts in the provided JSON. Do not add facts, context, motives or speculation.
2. Never state or imply that any person or entity committed fraud, a crime, or wrongdoing. Describe observations as
   unverified discrepancies, anomalies or patterns (e.g. "Unverified physical discrepancy", "High-variance
   award-to-presence ratio", "Anomalous co-location pattern").
3. Reproduce numbers and dollar amounts exactly as written in the facts. Do not round, total or compute new numbers.
4. State that the indicators require independent verification and may have lawful explanations.
5. Plain prose, 120-220 words, 1-2 paragraphs, no headings, no bullet points, no legal conclusions."""


# --------------------------------------------------------------------------- #
# Synthesizer
# --------------------------------------------------------------------------- #


def _money(x: float) -> str:
    return f"${x:,.2f}"


class Synthesizer:
    def __init__(self, backend: LLMBackend | None = None, guard: LanguageGuard | None = None):
        self.backend = backend or TemplateBackend()
        self.guard = guard or LanguageGuard()

    # -- facts & narrative ------------------------------------------------- #

    @staticmethod
    def fact_sheet(entity: AwardeeEntity, score: ScoreBreakdown, enrichment: EnrichmentData) -> dict[str, Any]:
        agencies = sorted({a.awarding_agency for a in entity.awards if a.awarding_agency})
        return {
            "entity_name": entity.name,
            "uei": entity.uei,
            "address_of_record": entity.address.one_line,
            "award_count": len(entity.awards),
            "total_awarded": _money(entity.total_awarded),
            "awarding_agencies": agencies,
            "score": score.score,
            "score_max": 100,
            "triggered_indicators": [{"title": f.title, "points": f.score_contribution, "observation": f.description} for f in score.flags],
            "indicators_with_insufficient_data": [ev.title for ev in score.evaluations if ev.status is RuleStatus.INSUFFICIENT_DATA],
            "premises_classification": enrichment.land_use.classification.value,
        }

    def template_summary(self, facts: dict[str, Any]) -> str:
        flags = facts["triggered_indicators"]
        n_awards = facts["award_count"]
        agencies = ", ".join(facts["awarding_agencies"]) or "the awarding agencies listed below"
        titles = "; ".join(f"{f['title']} (+{f['points']})" for f in flags) or "none"
        p1 = (
            f"This briefing summarizes an automated, rule-based screen of public records concerning {facts['entity_name']}"
            f"{' (UEI ' + facts['uei'] + ')' if facts['uei'] else ''}, address of record {facts['address_of_record'] or 'not published'}. "
            f"Within the scan scope the recipient holds {n_awards} federal award{'s' if n_awards != 1 else ''} totaling "
            f"{facts['total_awarded']} from {agencies}. The deterministic screen assigned an anomaly score of "
            f"{facts['score']}/{facts['score_max']} from {len(flags)} triggered indicator{'s' if len(flags) != 1 else ''}: {titles}."
        )
        p2 = (
            "Each indicator describes an unverified discrepancy between public data sources (for example, a high-variance "
            "award-to-presence ratio or an anomalous co-location pattern) and does not establish wrongdoing by any person "
            "or entity. Lawful explanations, including stale registry data, legitimate home-based or shared-office "
            "operations, and undisclosed affiliations, must be ruled out through the ground-truthing checklist before any "
            "referral is considered."
        )
        if facts["indicators_with_insufficient_data"]:
            p2 += (" The following indicators could not be evaluated for lack of data and contributed no points: "
                   + "; ".join(facts["indicators_with_insufficient_data"]) + ".")
        return f"{p1}\n\n{p2}"

    def narrative(self, facts: dict[str, Any]) -> tuple[str, NarrativeProvenance]:
        template = self.template_summary(facts)
        if isinstance(self.backend, TemplateBackend):
            return template, NarrativeProvenance(backend="template")
        prov = NarrativeProvenance(backend=self.backend.name, model=self.backend.model)
        try:
            text = self.backend.complete(SYSTEM_PROMPT, "Facts (JSON):\n" + json.dumps(facts, indent=2, default=str))
        except Exception as exc:  # noqa: BLE001 - any provider failure falls back to the template
            log.warning("LLM backend %s failed (%s); using template.", self.backend.name, exc)
            prov.fell_back_to_template, prov.fallback_reason = True, f"backend error: {type(exc).__name__}: {exc}"[:500]
            return template, prov
        problems = self.guard.violations(text, facts) if text else ["empty response"]
        if problems:
            log.warning("LLM narrative rejected by LanguageGuard: %s", problems)
            prov.fell_back_to_template, prov.fallback_reason = True, "LanguageGuard: " + "; ".join(problems)[:500]
            return template, prov
        return text, prov

    # -- dossier ----------------------------------------------------------- #

    def build_dossier(
        self,
        entity: AwardeeEntity,
        enrichment: EnrichmentData,
        score: ScoreBreakdown,
        context: ScoringContext,
        sources: list[SourceRecord],
        scan: ScanContext,
        vertical_caveats: list[str] | None = None,
    ) -> FraudRiskDossier:
        generated_at = utcnow()
        facts = self.fact_sheet(entity, score, enrichment)
        summary, prov = self.narrative(facts)

        triggered = [f.rule_id for f in score.flags]
        statute_keys = ["FCA", "FCA_PDB", "WIRE", "MAIL"]
        for rid in triggered:
            statute_keys += [k for k in RULE_STATUTES.get(rid, []) if k not in statute_keys]
        if entity.is_healthcare and "HC_FRAUD" not in statute_keys:
            statute_keys.append("HC_FRAUD")
        if entity.total_awarded > 10_000 and "PROGRAM_THEFT" not in statute_keys:
            statute_keys.append("PROGRAM_THEFT")
        if entity.total_awarded <= 150_000:
            statute_keys.append("PFCRA")

        checklist = [item for rid in triggered for item in RULE_CHECKLIST.get(rid, [])] + BASE_CHECKLIST

        related_keys = sorted({k for f in score.flags for k in f.related_entity_keys})
        related = []
        for k in related_keys:
            other = context.entities.get(k)
            if other:
                related.append({
                    "entity_key": k, "name": other.name, "uei": other.uei, "address": other.address.one_line,
                    "total_awarded": other.total_awarded,
                    "officers": [o.name for o in other.officers],
                    "linked_by": [f.rule_id for f in score.flags if k in f.related_entity_keys],
                })

        caveats = list(COMMON_CAVEATS) + list(vertical_caveats or [])
        if enrichment.land_use.classification in (LandUseClass.RESIDENTIAL, LandUseClass.MIXED):
            caveats.append("Residential classification derives from OSM building/land-use tags, not municipal zoning; verify with the local zoning map and assessor class code.")
        if entity.formation_date_source and "proxy" in entity.formation_date_source:
            caveats.append("The formation date used is a proxy (NPI enumeration date), not a legal formation date.")
        caveats += enrichment.notes

        return FraudRiskDossier(
            dossier_id=FraudRiskDossier.make_id(entity.entity_key, generated_at),
            generated_at=generated_at,
            scan=scan,
            entity=entity,
            enrichment=enrichment,
            score=score,
            related_entities=related,
            statutory_references=[{"key": k, **STATUTES[k]} for k in statute_keys],
            ground_truthing_checklist=checklist,
            caveats=caveats,
            narrative_provenance=prov,
            executive_summary=summary,
            sources=sources,
            disclaimer=DISCLAIMER,
        )

    # -- rendering --------------------------------------------------------- #

    @staticmethod
    def _cell(value: Any) -> str:
        text = "" if value is None else str(value)
        return text.replace("|", "\\|").replace("\n", " ")

    def render_markdown(self, d: FraudRiskDossier) -> str:
        e, s, enr = d.entity, d.score, d.enrichment
        c = self._cell
        out: list[str] = []
        w = out.append

        w(f"# Investigative Lead Briefing: {e.name}\n")
        w("> **LEAD FOR HUMAN REVIEW. NOT A FINDING OF WRONGDOING.** Generated automatically from public records; "
          "no report has been submitted to any agency.\n")
        w("| Field | Value |\n|---|---|")
        w(f"| Dossier ID | `{d.dossier_id}` |")
        w(f"| Generated (UTC) | {d.generated_at:%Y-%m-%d %H:%M:%S} |")
        w(f"| Anomaly score | **{s.score} / 100** (raw total {s.raw_total} of {s.max_possible} possible; review threshold {d.scan.threshold}) |")
        w(f"| Scan scope | state={d.scan.state or 'any'}; vertical={d.scan.vertical or 'custom'}; min award={_money(d.scan.min_award) if d.scan.min_award else 'none'}; "
          f"period={d.scan.start_date or '2007-10-01'} to {d.scan.end_date or 'today'} |")
        w(f"| Narrative author | {d.narrative_provenance.backend}{' / ' + d.narrative_provenance.model if d.narrative_provenance.model else ''}"
          f"{' (fell back to template: ' + c(d.narrative_provenance.fallback_reason) + ')' if d.narrative_provenance.fell_back_to_template else ''} |")
        w("")

        w("## 1. Executive Summary\n")
        w(d.executive_summary + "\n")

        w("## 2. Entity Profile\n")
        w("| Attribute | Value |\n|---|---|")
        w(f"| Legal name (USASpending) | {c(e.name)} |")
        if e.alternate_names:
            w(f"| Alternate names | {c('; '.join(e.alternate_names))} |")
        w(f"| UEI / DUNS / CAGE | {e.uei or 'n/a'} / {e.duns or 'n/a'} / {e.cage or 'n/a'} |")
        w(f"| Parent | {c(e.parent_name) or 'n/a'} ({e.parent_uei or 'n/a'}) |")
        w(f"| Address of record | {c(e.address.one_line) or 'n/a'} |")
        w(f"| Normalized location key | `{e.address.location_key or 'n/a'}` |")
        w(f"| Business types | {c(', '.join(e.business_types)) or 'n/a'} |")
        w(f"| Formation date | {e.formation_date or 'unknown'}{' (' + c(e.formation_date_source) + ')' if e.formation_date_source else ''} |")
        w(f"| Healthcare classification | {'yes: ' + c(e.healthcare_basis) if e.is_healthcare else 'no'} |")
        w(f"| Officers / officials | {c('; '.join(f'{o.name} ({o.title or o.role_source})' for o in e.officers)) or 'none published'} |")
        w(f"| Total awarded (in scope) | **{_money(e.total_awarded)}** across {len(e.awards)} award(s) |")
        w("")
        w("| Award ID | Type | Agency | Amount | Signed / Start | NAICS / PSC / Listing |\n|---|---|---|---:|---|---|")
        for a in sorted(e.awards, key=lambda a: a.amount, reverse=True)[:15]:
            codes = " / ".join(x for x in (a.naics_code, a.psc_code, ", ".join(a.program_numbers)) if x) or "n/a"
            w(f"| {c(a.award_id)} | {c(a.award_type or a.award_group)} | {c(a.awarding_agency)} | {_money(a.amount)} | {a.effective_date or 'n/a'} | {c(codes)} |")
        if len(e.awards) > 15:
            w(f"\n_{len(e.awards) - 15} smaller award(s) omitted; see JSON dossier._")
        w("")

        w("## 3. Deterministic Score Breakdown\n")
        w(f"Scorer version `{s.scorer_version}`. Formula: `{s.formula}`\n")
        w("| Rule | Weight | Status | Points | Rationale |\n|---|---:|---|---:|---|")
        for ev in s.evaluations:
            status = {"triggered": "**TRIGGERED**"}.get(ev.status.value, ev.status.value.replace("_", " "))
            w(f"| {ev.rule_id}: {ev.title} | {ev.weight} | {status} | {ev.points_awarded} | {c(ev.rationale)} |")
        w(f"| **Total** | {s.max_possible} | | **{s.raw_total}** → score **{s.score}** | |")
        w("")

        w("## 4. Evidence Matrix\n")
        if not s.flags:
            w("_No indicators triggered._\n")
        src_by_id = {r.record_id: r for r in d.sources}
        for f in s.flags:
            w(f"### {f.rule_id}: {f.title} (+{f.score_contribution})\n")
            w("| # | Observation (field) | Observed value | Source record | Endpoint | Retrieved (UTC) |\n|---|---|---|---|---|---|")
            for i, ev in enumerate(f.evidence, 1):
                rec = src_by_id.get(ev.source_record_id or "")
                val = ev.observed_value if isinstance(ev.observed_value, str) else json.dumps(ev.observed_value, default=str)
                if len(val) > 260:
                    val = val[:257] + "..."
                endpoint = f"`{rec.method} {rec.url[:110]}{'…' if len(rec.url) > 110 else ''}`" if rec else "derived"
                when = f"{ev.retrieved_at:%Y-%m-%d %H:%M:%S}" if ev.retrieved_at else "n/a"
                note = f" _({c(ev.note)})_" if ev.note else ""
                w(f"| {i} | {c(ev.field)}{note} | {c(val)} | `{ev.source_record_id or 'n/a'}` | {endpoint} | {when} |")
            w("")

        w("## 5. Specific Inconsistencies\n")
        for f in s.flags:
            w(f"- **{f.title}.** {f.description}")
        for sig in enr.land_use.signals:
            w(f"- _Premises signal ({enr.land_use.classification.value})_: {sig}")
        if enr.geocode.performed:
            if enr.geocode.matched:
                w(f"- _Geocode_: Census matched `{enr.geocode.matched_address}` at ({enr.geocode.latitude}, {enr.geocode.longitude}), TIGER line {enr.geocode.tiger_line_id}.")
            else:
                w(f"- _Geocode_: address of record did not match a Census TIGER street segment{' (' + c(enr.geocode.error) + ')' if enr.geocode.error else ''}.")
        if enr.npi.performed:
            if enr.npi.best_match:
                b = enr.npi.best_match
                w(f"- _NPPES_: best match NPI {b.npi} '{b.organization_name}', status {b.status}, enumerated {b.enumeration_date}, "
                  f"name similarity {b.name_similarity:.0f}, address match {'yes' if b.address_match else 'no'}.")
            else:
                w(f"- _NPPES_: no accepted organizational NPI match for queries {', '.join(f'`{q}`' for q in enr.npi.query_names)} "
                  f"({enr.npi.candidates_examined} candidates examined).")
        if not s.flags and not enr.land_use.signals:
            w("- None recorded.")
        w("")

        w("## 6. Related Entities\n")
        if d.related_entities:
            w("| Entity | UEI | Address | Total awarded | Officers | Linked by |\n|---|---|---|---:|---|---|")
            for r in d.related_entities:
                w(f"| {c(r['name'])} | {r['uei'] or 'n/a'} | {c(r['address'])} | {_money(r['total_awarded'])} | {c('; '.join(r['officers']))} | {', '.join(r['linked_by'])} |")
        else:
            w("_None identified within scan scope._")
        w("")

        w("## 7. Statutory Cross-References\n")
        w("_Frameworks a reviewing authority may consider if, and only if, independent verification substantiates the "
          "observations. Listing a statute is not a legal conclusion. Consult qualified counsel._\n")
        for st in d.statutory_references:
            w(f"- **{st['name']}**, {st['citation']}: {st['relevance']}")
        w("")

        w("## 8. Recommended Ground-Truthing Checklist\n")
        for item in d.ground_truthing_checklist:
            w(f"- [ ] {item}")
        w("")

        w("## 9. Caveats & Limitations\n")
        for cv in d.caveats:
            w(f"- {cv}")
        w("")

        w("## 10. Source Ledger (Chain of Custody)\n")
        w("| Record | Source | Request | HTTP | Retrieved (UTC) | Attempts | SHA-256 (prefix) |\n|---|---|---|---|---|---:|---|")
        for r in d.sources:
            w(f"| `{r.record_id}` | {r.source} | `{r.method} {c(r.url)}` | {r.status_code or c(r.error)} | {r.retrieved_at:%Y-%m-%d %H:%M:%S} | {r.attempts} | `{(r.response_sha256 or '')[:16]}` |")
        w("\nFull raw response snapshots and request bodies are embedded in the companion JSON dossier (`sources[].raw_snippet`).\n")

        w("---\n")
        w(f"_{d.disclaimer}_\n")
        return "\n".join(out)

    def write(self, dossier: FraudRiskDossier, output_dir: Path) -> tuple[Path, Path]:
        output_dir.mkdir(parents=True, exist_ok=True)
        slug = re.sub(r"[^A-Za-z0-9]+", "_", dossier.entity.name).strip("_")[:60] or "entity"
        base = output_dir / f"{dossier.dossier_id}_{slug}"
        json_path, md_path = base.with_suffix(".json"), base.with_suffix(".md")
        json_path.write_text(dossier.model_dump_json(indent=2), encoding="utf-8")
        md_path.write_text(self.render_markdown(dossier), encoding="utf-8")
        return json_path, md_path
