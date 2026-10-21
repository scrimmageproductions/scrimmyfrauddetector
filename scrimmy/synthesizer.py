"""Stage: Synthesis & Dossier Generation.

Division of labour:

* Everything numeric or evidentiary (score, flags, capacity arithmetic, evidence
  matrix, sources, hashes, statutes, track routing, checklists) is rendered
  deterministically from the data models.
* An optional LLM writes *only* the Executive Summary prose, from a closed fact
  sheet. Its output must pass ``LanguageGuard`` (no accusatory language, no numbers
  absent from the fact sheet). On any failure the deterministic template is used
  instead, and the fallback is recorded in the dossier.

Backends: ``template`` (default, offline), ``anthropic``, ``openai``, ``ollama``
(local HTTP), ``litellm`` (any provider LiteLLM supports).
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
    NetworkSummary,
    RuleStatus,
    ScanContext,
    ScoreBreakdown,
    SourceRecord,
    TrackAssessment,
    utcnow,
)
from .scorer import (
    CO_LOCATED_SHELL_CLUSTER,
    EXECUTIVE_SERIAL_REGISTRATION,
    HIGH_DOLLAR_RESIDENTIAL_ZONE,
    LEIE_EXCLUSION_MATCH,
    MATHEMATICAL_IMPOSSIBILITY,
    NEWLY_FORMED_WINDFALL,
    PHANTOM_HEALTHCARE_PROVIDER,
    PHYSICAL_SITE_MISMATCH,
    UNRESOLVED_PHYSICAL_DISCREPANCY,
    ScoringContext,
)

log = logging.getLogger(__name__)

DISCLAIMER = (
    "This dossier is an automated investigative LEAD generated from public records for review by a qualified human "
    "investigator and counsel. It is not a finding, allegation or accusation of wrongdoing by any person or entity. Every "
    "indicator describes an unverified discrepancy in public data that may have a lawful explanation (data-entry error, "
    "stale registry records, legitimate home-based operations, shared professional office space, sponsor or pass-through "
    "funding, affiliated entities not linked in public data, etc.). No report has been submitted to any agency by this tool. "
    "Do not publish, circulate or act on this document without independent verification."
)

# --------------------------------------------------------------------------- #
# Statutes and checklists (deterministic content)
# --------------------------------------------------------------------------- #

STATUTES: dict[str, dict[str, str]] = {
    "FCA": {"citation": "31 U.S.C. §§ 3729-3733", "name": "False Claims Act",
            "relevance": "Civil liability for knowingly presenting false claims for federal funds. Qui tam actions are filed "
                         "under seal by counsel (§ 3730(b)). Relator share under § 3730(d): generally 15-25% if the government "
                         "intervenes, 25-30% if it declines, and up to 10% where the action rests primarily on publicly "
                         "disclosed information. § 3730(h) protects against retaliation."},
    "FCA_PDB": {"citation": "31 U.S.C. § 3730(e)(4)", "name": "False Claims Act: public disclosure bar",
                "relevance": "Qui tam actions based substantially on publicly disclosed information (including federal reports "
                             "and databases such as USASpending, NPPES and the LEIE) may be dismissed unless the relator is an "
                             "'original source' whose independent knowledge materially adds to the public disclosures. Analysis "
                             "of public data alone may not qualify; consult counsel before filing."},
    "MAIL": {"citation": "18 U.S.C. § 1341", "name": "Mail fraud",
             "relevance": "Use of the mails in a scheme to obtain money by false pretenses."},
    "WIRE": {"citation": "18 U.S.C. § 1343", "name": "Wire fraud",
             "relevance": "Use of interstate wire communications (including electronic claims, payment and grant-drawdown "
                          "systems) in a scheme to obtain money by false pretenses."},
    "FALSE_STMT": {"citation": "18 U.S.C. § 1001", "name": "False statements",
                   "relevance": "Materially false statements in matters within federal jurisdiction (e.g. SAM.gov registrations, "
                                "grant applications, attendance or meal-count certifications)."},
    "PROGRAM_THEFT": {"citation": "18 U.S.C. § 666", "name": "Theft concerning programs receiving federal funds",
                      "relevance": "Applies to organizations receiving more than $10,000 in federal program benefits in a one-year period."},
    "CONSPIRACY": {"citation": "18 U.S.C. § 371", "name": "Conspiracy to defraud the United States",
                   "relevance": "Relevant where multiple coordinated entities or individuals are involved."},
    "HC_FRAUD": {"citation": "18 U.S.C. § 1347", "name": "Health care fraud",
                 "relevance": "Schemes to defraud any health care benefit program, including Medicare and Medicaid."},
    "CMPL": {"citation": "42 U.S.C. § 1320a-7a", "name": "Civil Monetary Penalties Law",
             "relevance": "Administrative penalties for false claims to federal health care programs, including claims for items "
                          "or services furnished, ordered or directed by an excluded individual or entity."},
    "EXCLUSION": {"citation": "42 U.S.C. § 1320a-7; 42 C.F.R. § 1001.1901", "name": "OIG exclusion authority and effect of exclusion",
                  "relevance": "Federal health care programs do not pay for items or services furnished, ordered or prescribed "
                               "by an excluded party, including administrative and management services."},
    "PFCRA": {"citation": "31 U.S.C. §§ 3801-3812", "name": "Program Fraud Civil Remedies Act",
              "relevance": "Administrative remedies for false claims and statements of $150,000 or less."},
}

RULE_STATUTES: dict[str, list[str]] = {
    LEIE_EXCLUSION_MATCH: ["EXCLUSION", "CMPL"],
    MATHEMATICAL_IMPOSSIBILITY: ["FALSE_STMT", "PROGRAM_THEFT"],
    PHANTOM_HEALTHCARE_PROVIDER: ["HC_FRAUD", "CMPL"],
    HIGH_DOLLAR_RESIDENTIAL_ZONE: ["FALSE_STMT"],
    PHYSICAL_SITE_MISMATCH: ["FALSE_STMT"],
    NEWLY_FORMED_WINDFALL: ["FALSE_STMT", "PROGRAM_THEFT"],
    CO_LOCATED_SHELL_CLUSTER: ["CONSPIRACY", "FALSE_STMT"],
    EXECUTIVE_SERIAL_REGISTRATION: ["CONSPIRACY", "FALSE_STMT"],
    UNRESOLVED_PHYSICAL_DISCREPANCY: ["FALSE_STMT"],
}

BASE_CHECKLIST = [
    "Re-run every source URL in the Source Ledger and confirm the responses still match the recorded SHA-256 hashes, or note what changed (run `sha256sum -c SHA256SUMS` in the evidence directory first).",
    "Pull the full award files (USASpending award pages, agency grant notices, Federal Audit Clearinghouse Single Audits) and record the stated place of performance, purpose and sub-recipients.",
    "Retrieve the Secretary of State filing history (formation date, registered agent, officers, status, annual reports) and save dated copies.",
    "Retrieve the SAM.gov entity registration (status, entity start date, physical vs. mailing address, POCs) and check SAM.gov exclusions.",
    "County deed / assessor audit: owner of record, property class code (residential/commercial), homestead exemption, transfer history, and whether the owner is connected to listed officers.",
    "State licensing: license type (family/home vs. center), licensed capacity, ages served, inspection and violation history at the address of record.",
    "Document every lawful explanation considered and why it was or was not ruled out.",
]

RULE_CHECKLIST: dict[str, list[str]] = {
    LEIE_EXCLUSION_MATCH: [
        "Confirm the LEIE match with the OIG online exclusion search (verify with SSN/EIN through the OIG site where you lawfully have them); check reinstatement and waiver status.",
        "Establish the excluded person's actual role (owner, officer, manager, billing or clinical staff) from state filings or provider-enrollment records.",
    ],
    MATHEMATICAL_IMPOSSIBILITY: [
        "Confirm the licensed capacity, ages served and operating schedule directly from the state licensing record, and check whether the recipient operates other licensed sites that the award also funds.",
        "Replace the benchmark unit rate with the program's actual rate for the award period (CACFP: annual USDA notice; hospice: CMS final rule; CCDF: state payment rates) and recompute.",
        "Obtain claimed meal/attendance/patient-day counts through official records requests (FOIA; state public-records law) and compare them to capacity x operating days.",
    ],
    PHANTOM_HEALTHCARE_PROVIDER: [
        "Search NPPES manually by name variants, DBA names, prior names and individual practitioners; check whether services were billed under an individual (Type 1) NPI or a parent organization.",
        "Check the state Medicaid provider enrollment directory, and CMS Provider Enrollment (PECOS) data where public.",
        "Confirm whether the funded activity actually required NPI enumeration (grant administration and research do not).",
    ],
    HIGH_DOLLAR_RESIDENTIAL_ZONE: [
        "From a public right-of-way only, photograph the premises with date/time/GPS metadata: signage, posted hours, deliveries. Do not trespass, enter, contact occupants, or photograph people.",
        "Check Street View / county imagery history for a business presence during the award period.",
        "If the address is a CMRA/PMB or P.O. Box, establish the actual place of performance from award documents, licensing records and NPPES practice locations.",
        "Check zoning and home-occupation permits with the municipality.",
    ],
    PHYSICAL_SITE_MISMATCH: [
        "Open the referenced imagery (Mapillary key or Google pano_id) and confirm the vision classification yourself; note the capture date relative to the award period.",
        "Conduct at least two public right-of-way observations during the hours the funded services should be operating (see SOP).",
    ],
    NEWLY_FORMED_WINDFALL: [
        "Confirm the formation date from the state filing (not an aggregator); check for predecessor entities, mergers or name changes.",
        "Compare the award solicitation/notice date with the formation date and determine whether past performance or experience was certified.",
    ],
    CO_LOCATED_SHELL_CLUSTER: [
        "For each co-located entity, pull state filings and compare registered agents, officers, formation dates and filing patterns.",
        "Rule out a legitimate multi-tenant office, registered-agent service, accountant's office or incubator at the address.",
    ],
    EXECUTIVE_SERIAL_REGISTRATION: [
        "Confirm the individual's identity across entities (full name, middle name, title, filings); common names produce false matches.",
        "Determine whether the entities are lawfully affiliated (franchise, management company, disclosed common ownership).",
    ],
    UNRESOLVED_PHYSICAL_DISCREPANCY: [
        "Verify the address with the USPS ZIP Code lookup and county parcel data; check for new construction, rural routes or typos.",
    ],
}

QUICK_PHYSICAL_CHECKLIST = [
    "Street View / aerial imagery at the geocoded point: is there a building with signage consistent with the funded service (child care, home health office, hospice)?",
    "State license lookup for the entity name: is there an active license at this address, and do the license type and capacity match section 6?",
    "NPPES search (healthcare recipients): is there an active Type-2 NPI whose practice location is this address?",
    "Secretary of State business search: is the entity active, and do the formation date and officers match this dossier?",
    "HHS-OIG LEIE online search for each listed officer or authorized official.",
    "If any check above resolves an indicator, mark it Explained and stop; otherwise continue with the full field checklist (section 12).",
]

FINAL_REVIEW_CHECKLIST = [
    "Every section 12 item is completed and documented, and each indicator is marked Explained, Unresolved or Corroborated.",
    "Each statement in the draft tip cites a primary record you retrieved yourself, not only this dossier.",
    "Observed facts are kept separate from inferences; the wording is neutral; no conclusions of guilt.",
    "Counsel has been consulted on routing (criminal tip vs. qui tam), the public disclosure bar, and original-source status.",
    "Nothing has been shared publicly or with the subject; the evidence folder hashes still verify.",
]

COMMON_CAVEATS = [
    "Scores reflect only the documented heuristics applied to public data at the time of retrieval. A high score is a prioritisation signal, not evidence of intent or wrongdoing.",
    "Cross-entity rules (co-location, serial registration) only consider recipients returned by this scan's filters.",
    "OpenStreetMap is volunteer-maintained; missing or outdated building/POI tags are common.",
    "Census Geocoder coordinates are interpolated along street segments and can be offset by tens of meters from the parcel.",
    "USASpending recipient addresses reflect the most recent registration data and may differ from the address at the time of award.",
    "Person-name matches use normalized names only and do not confirm that two records refer to the same individual.",
]

TRACK_A_RULES = {PHANTOM_HEALTHCARE_PROVIDER, PHYSICAL_SITE_MISMATCH, MATHEMATICAL_IMPOSSIBILITY, NEWLY_FORMED_WINDFALL,
                 HIGH_DOLLAR_RESIDENTIAL_ZONE, LEIE_EXCLUSION_MATCH}
TRACK_B_RULES = {CO_LOCATED_SHELL_CLUSTER, EXECUTIVE_SERIAL_REGISTRATION}


def assess_track(score: ScoreBreakdown, network: NetworkSummary, entity: AwardeeEntity) -> TrackAssessment:
    """Deterministic routing *suggestion*. The actual choice of channel is made by the human and their counsel."""
    triggered = {ev.rule_id for ev in score.evaluations if ev.status is RuleStatus.TRIGGERED and ev.points_awarded > 0}
    a_hits, b_hits = sorted(triggered & TRACK_A_RULES), sorted(triggered & TRACK_B_RULES)
    rationale = []
    multi = len(network.component_entities) >= 3
    if a_hits:
        rationale.append(f"Entity-level indicators ({', '.join(a_hits)}) concern a single recipient's registration, premises or "
                         f"capacity, which suits a concentrated criminal referral (Track A).")
    if b_hits:
        rationale.append(f"Network indicators ({', '.join(b_hits)}) link this recipient to other awardees, the multi-entity "
                         f"pattern counsel evaluates for a civil False Claims Act / qui tam action (Track B).")
    if multi:
        rationale.append(f"Connected component: {len(network.component_entities)} awardees, "
                         f"${network.component_total_awarded:,.2f} total awarded in scope.")
    if LEIE_EXCLUSION_MATCH in triggered or (MATHEMATICAL_IMPOSSIBILITY in triggered and entity.is_healthcare):
        rationale.append("Exclusion and reimbursement-volume indicators are also commonly pursued civilly (CMPL / FCA).")
    track_b = bool(b_hits) or (multi and bool(a_hits)) or LEIE_EXCLUSION_MATCH in triggered
    track_a = bool(a_hits)
    primary = "B: civil FCA / qui tam (via counsel)" if (b_hits or (multi and a_hits)) else "A: criminal referral" if track_a else "Undetermined"
    rationale.append("Routing is a suggestion for counsel. A qui tam complaint must be filed under seal by an attorney, and DOJ "
                     "whistleblower programs have their own eligibility rules and reward terms (check justice.gov for the current terms).")
    return TrackAssessment(track_a_criminal_referral=track_a, track_b_civil_fca=track_b, primary=primary, rationale=rationale)


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
    def __init__(self, backend: LLMBackend | None = None, guard: LanguageGuard | None = None, high_priority_threshold: int = 50):
        self.backend = backend or TemplateBackend()
        self.guard = guard or LanguageGuard()
        self.high_priority_threshold = high_priority_threshold

    # -- facts & narrative ------------------------------------------------- #

    @staticmethod
    def fact_sheet(entity: AwardeeEntity, score: ScoreBreakdown, enrichment: EnrichmentData,
                   track: TrackAssessment | None = None) -> dict[str, Any]:
        agencies = sorted({a.awarding_agency for a in entity.awards if a.awarding_agency})
        cap = enrichment.capacity
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
            "capacity_ratio": cap.capacity_ratio if cap.performed else None,
            "licensed_capacity": cap.licensed_capacity if cap.performed else None,
            "suggested_track": track.primary.split(":")[0] if track and ":" in track.primary else None,
            "strike_force_district": enrichment.strike_force.district if enrichment.strike_force else None,
            "strike_force_hub": enrichment.strike_force.hub_label if enrichment.strike_force else None,
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
        if facts.get("strike_force_district"):
            p1 += f" The address of record lies in the {facts['strike_force_district']} ({facts['strike_force_hub']} preset)."
        if facts.get("capacity_ratio") is not None:
            p1 += (f" Benchmark arithmetic implies daily beneficiary volume of {facts['capacity_ratio']}x the comparison capacity "
                   f"of {facts['licensed_capacity']}.")
        if facts.get("suggested_track"):
            p1 += f" Suggested routing for counsel's consideration: Track {facts['suggested_track']} (see table below)."
        p2 = (
            "Each indicator describes an unverified discrepancy between public data sources (for example, a statistically "
            "anomalous disbursement-to-presence ratio or unresolved corporate co-location) and does not establish wrongdoing "
            "by any person or entity. Lawful explanations, including stale registry data, legitimate home-based or shared-office "
            "operations, sponsor or pass-through funding, and undisclosed affiliations, must be ruled out through the field "
            "checklist before any referral is considered."
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
        network = context.graph.summary(entity.entity_key)
        track = assess_track(score, network, entity)
        facts = self.fact_sheet(entity, score, enrichment, track)
        summary, prov = self.narrative(facts)

        triggered = [ev.rule_id for ev in score.evaluations if ev.status is RuleStatus.TRIGGERED]
        statute_keys = ["FCA", "FCA_PDB", "WIRE", "MAIL"]
        for rid in triggered:
            statute_keys += [k for k in RULE_STATUTES.get(rid, []) if k not in statute_keys]
        for cond, key in ((entity.is_healthcare, "HC_FRAUD"), (entity.total_awarded > 10_000, "PROGRAM_THEFT"),
                          (len(network.component_entities) >= 2, "CONSPIRACY"), (entity.total_awarded <= 150_000, "PFCRA")):
            if cond and key not in statute_keys:
                statute_keys.append(key)

        checklist = [item for rid in triggered for item in RULE_CHECKLIST.get(rid, [])] + BASE_CHECKLIST

        related = []
        for k in sorted({k for f in score.flags for k in f.related_entity_keys}):
            other = context.entities.get(k)
            if other:
                related.append({"entity_key": k, "name": other.name, "uei": other.uei, "address": other.address.one_line,
                                "total_awarded": other.total_awarded, "officers": [o.name for o in other.officers],
                                "linked_by": [f.rule_id for f in score.flags if k in f.related_entity_keys]})

        caveats = list(COMMON_CAVEATS) + list(vertical_caveats or [])
        if enrichment.land_use.classification in (LandUseClass.RESIDENTIAL, LandUseClass.MIXED):
            caveats.append("Residential classification derives from OSM building/land-use tags, not municipal zoning; verify with the zoning map and assessor class code.")
        if enrichment.capacity.performed:
            caveats.append("Capacity arithmetic assumes the entire award paid for beneficiary-days at the matched licensed site at the benchmark "
                           "rate. Sponsors, multi-site operators, administrative/capital funding and unspent obligations all break that assumption.")
        if enrichment.vision.assessment:
            caveats.append("Site classification is a vision model's description of one street-level image, which may be outdated, obstructed or "
                           "facing a neighbouring parcel. Confirm it in person.")
        if enrichment.leie.matches:
            caveats.append("LEIE matches are on published name/state/location/NPI fields. Confirm identity through the OIG online search before relying on them.")
        if entity.formation_date_source and "proxy" in entity.formation_date_source:
            caveats.append("The formation date used is a proxy (NPI enumeration date), not a legal formation date.")
        caveats += enrichment.notes

        return FraudRiskDossier(
            dossier_id=FraudRiskDossier.make_id(entity.entity_key, generated_at), generated_at=generated_at, scan=scan,
            entity=entity, enrichment=enrichment, score=score, related_entities=related, network=network, track=track,
            statutory_references=[{"key": k, **STATUTES[k]} for k in statute_keys], ground_truthing_checklist=checklist,
            caveats=caveats, narrative_provenance=prov, executive_summary=summary, sources=sources, disclaimer=DISCLAIMER,
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
        priority = "HIGH-PRIORITY FORENSIC LEAD" if s.score >= self.high_priority_threshold else "Below high-priority threshold"

        w(f"# Investigative Lead Briefing: {e.name}\n")
        w("> **LEAD FOR HUMAN REVIEW. NOT A FINDING OF WRONGDOING.** Generated automatically from public records; "
          "no report has been submitted to any agency.\n")
        w("| Field | Value |\n|---|---|")
        w(f"| Dossier ID | `{d.dossier_id}` |")
        w(f"| Generated (UTC) | {d.generated_at:%Y-%m-%d %H:%M:%S} |")
        w(f"| Classification | **{priority}**: score **{s.score} / 100** (raw {s.raw_total} of {s.max_possible}; profile `{d.scan.scoring_profile}`; threshold {d.scan.threshold}) |")
        if d.track:
            w(f"| Suggested routing | {c(d.track.primary)} |")
        sf = enr.strike_force
        if sf:
            w(f"| Strike Force target | {c(sf.hub_label)}: {c(sf.district) if sf.district else 'outside the preset districts'} |")
        w(f"| Scan scope | state={d.scan.state or 'any'}; vertical={d.scan.vertical or 'custom'}; min award={_money(d.scan.min_award) if d.scan.min_award else 'none'}; "
          f"period={d.scan.start_date or '2007-10-01'} to {d.scan.end_date or 'today'} |")
        if d.evidence_dir:
            w(f"| Evidence directory | `{d.evidence_dir}` (verify with `sha256sum -c SHA256SUMS`) |")
        w(f"| Narrative author | {d.narrative_provenance.backend}{' / ' + d.narrative_provenance.model if d.narrative_provenance.model else ''}"
          f"{' (fell back to template: ' + c(d.narrative_provenance.fallback_reason) + ')' if d.narrative_provenance.fell_back_to_template else ''} |")
        w("")

        w("## 1. Executive Summary & Lead Classification\n")
        w(d.executive_summary + "\n")
        if d.track:
            w("| Track | Indicated | Basis |\n|---|---|---|")
            w(f"| A: criminal referral (e.g. agency OIG / DOJ whistleblower program) | {'yes' if d.track.track_a_criminal_referral else 'no'} | entity-level registration, premises, capacity or exclusion indicators |")
            w(f"| B: civil False Claims Act / qui tam (via counsel) | {'yes' if d.track.track_b_civil_fca else 'no'} | multi-entity network, exclusion or reimbursement-volume indicators |")
            w("")
            for r in d.track.rationale:
                w(f"- {r}")
            w("")

        w("## 2. Strike Force Targeting\n")
        if sf:
            w("| Field | Value |\n|---|---|")
            w(f"| Preset | {c(sf.hub_label)} |")
            w(f"| Federal judicial district | {c(sf.district) or 'not among the preset districts'} |")
            w(f"| U.S. Attorney's Office | {c(sf.us_attorney_office) or 'n/a'} |")
            w(f"| County | {c(sf.county_name) or 'n/a'}{' (FIPS ' + sf.county_fips + ')' if sf.county_fips else ''} |")
            w(f"| Determined from | {c(sf.basis)} |")
            w("\n_Health care fraud Strike Forces pair the U.S. Attorney's Office for the district with the DOJ Criminal Division "
              "Fraud Section's Health Care Fraud Unit. Presets are targeting groupings; confirm the district at uscourts.gov._\n")
        else:
            w("_No Strike Force preset was selected for this scan._\n")

        w("## 3. Entity Identity & Federal Award Summary\n")
        npi = enr.npi.best_match
        w("| Attribute | Value |\n|---|---|")
        w(f"| Legal name (USASpending) | {c(e.name)} |")
        if e.alternate_names:
            w(f"| Alternate names | {c('; '.join(e.alternate_names))} |")
        w(f"| UEI / DUNS / CAGE | {e.uei or 'n/a'} / {e.duns or 'n/a'} / {e.cage or 'n/a'} |")
        w(f"| Organizational NPI | {f'{npi.npi} ({npi.organization_name}, status {npi.status})' if npi else ('none matched' if enr.npi.performed else 'not checked')} |")
        w(f"| Parent | {c(e.parent_name) or 'n/a'} ({e.parent_uei or 'n/a'}) |")
        w(f"| Address of record | {c(e.address.one_line) or 'n/a'} |")
        w(f"| Normalized location key | `{e.address.location_key or 'n/a'}` |")
        w(f"| Business types | {c(', '.join(e.business_types)) or 'n/a'} |")
        w(f"| Formation date | {e.formation_date or 'unknown'}{' (' + c(e.formation_date_source) + ')' if e.formation_date_source else ''} |")
        w(f"| Healthcare classification | {'yes: ' + c(e.healthcare_basis) if e.is_healthcare else 'no'} |")
        w(f"| Officers / officials / agents | {c('; '.join(f'{o.name} ({o.title or o.role_source})' for o in e.officers)) or 'none published'} |")
        w(f"| Total awarded (in scope) | **{_money(e.total_awarded)}** across {len(e.awards)} award(s) |")
        w("")
        w("| Award ID | Type | Agency | Amount | Signed / Start | End | NAICS / PSC / Listing |\n|---|---|---|---:|---|---|---|")
        for a in sorted(e.awards, key=lambda a: a.amount, reverse=True)[:20]:
            codes = " / ".join(x for x in (a.naics_code, a.psc_code, ", ".join(a.program_numbers)) if x) or "n/a"
            w(f"| {c(a.award_id)} | {c(a.award_type or a.award_group)} | {c(a.awarding_agency)} | {_money(a.amount)} | {a.effective_date or 'n/a'} | {a.end_date or 'n/a'} | {c(codes)} |")
        if len(e.awards) > 20:
            w(f"\n_{len(e.awards) - 20} smaller award(s) omitted; see JSON dossier._")
        w("")

        w("## 4. Flagged Discrepancies & Heuristic Breakdown\n")
        w(f"Scorer version `{s.scorer_version}`. Formula: `{s.formula}`\n")
        w("| Rule | Weight | Status | Points | Rationale |\n|---|---:|---|---:|---|")
        for ev in s.evaluations:
            status = "**TRIGGERED**" if ev.status is RuleStatus.TRIGGERED and ev.points_awarded else (
                "triggered (unscored in profile)" if ev.status is RuleStatus.TRIGGERED else ev.status.value.replace("_", " "))
            w(f"| `{ev.rule_id}`: {ev.title} | {ev.weight} | {status} | {ev.points_awarded} | {c(ev.rationale)} |")
        w(f"| **Total** | {s.max_possible} | | **{s.raw_total}** → score **{s.score}** | |")
        w("")

        w("## 5. Evidence Matrix\n")
        if not s.flags:
            w("_No scored indicators._\n")
        src_by_id = {r.record_id: r for r in d.sources}
        for f in s.flags:
            w(f"### `{f.rule_id}`: {f.title} (+{f.score_contribution})\n")
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

        w("## 6. Mathematical Discrepancy Analysis\n")
        cap = enr.capacity
        lic = enr.license.best_match
        if lic:
            w(f"Matched state license: **{c(lic.facility_name)}**, license {lic.license_number or 'n/a'}, type {c(lic.license_type) or 'n/a'}, "
              f"status {c(lic.status) or 'n/a'}, capacity **{lic.capacity if lic.capacity is not None else 'n/a'}** "
              f"(source `{lic.source}`, name similarity {lic.name_similarity:.0f}, address match {'yes' if lic.address_match else 'no'}).\n")
        elif enr.license.performed:
            w(f"No state license matched ({enr.license.candidates_examined} candidates examined in {', '.join(enr.license.sources_queried)})"
              f"{': ' + c(enr.license.error) if enr.license.error else ''}.\n")
        if cap.performed:
            w("| Input | Value |\n|---|---|")
            w(f"| Benchmark | {c(cap.benchmark_label)} |")
            w(f"| Statutory unit rate | {_money(cap.unit_rate or 0)} per unit |")
            w(f"| Units per beneficiary-day | {cap.units_per_beneficiary_day:g} |")
            w(f"| Operating days / year | {cap.operating_days_per_year} |")
            w(f"| Award total (benchmark-mapped awards) | {_money(cap.award_total or 0)} |")
            w(f"| Period | {cap.period_start} to {cap.period_end} ({cap.period_years} yr; annualized over max(1, years)) |")
            w(f"| Annualized disbursement | {_money(cap.annualized_disbursement or 0)} |")
            w(f"| Estimated daily beneficiaries | **{cap.estimated_daily_beneficiaries:,.2f}** |")
            w(f"| {'Licensed capacity (state license)' if cap.capacity_basis == 'state_license' else 'Facility-type plausibility ceiling (no license matched)'} | **{cap.licensed_capacity}** |")
            w(f"| Ratio (threshold > {cap.ratio_threshold}) | **{cap.capacity_ratio:.3f}x** |")
            w(f"\n`{cap.formula}`\n\n_Rate source: {c(cap.benchmark_citation)}_\n")
        else:
            w(f"_Not computed: {c(cap.reason_not_performed) or 'no capacity data'}_\n")

        w("## 7. Physical Site Verification\n")
        geo = enr.geocode
        if geo.performed:
            w(f"- **Census Geocoder**: {'matched `' + c(geo.matched_address) + f'` at ({geo.latitude}, {geo.longitude}), TIGER line {geo.tiger_line_id}' if geo.matched else 'no address match' + (' (' + c(geo.error) + ')' if geo.error else '')}.")
        w(f"- **OpenStreetMap land use**: `{enr.land_use.classification.value}`" + (f" (nearest building tag `{enr.land_use.nearest_building_tag}`)" if enr.land_use.nearest_building_tag else "") + ".")
        for sig in enr.land_use.signals:
            w(f"  - {c(sig)}")
        v = enr.vision
        if v.assessment and v.imagery:
            im, a = v.imagery, v.assessment
            w(f"- **Street-level vision** ({c(v.model)}): `{a.category.value}`, confidence {a.confidence:.2f}; markers: {c(', '.join(a.physical_markers)) or 'none'}.")
            w(f"  - Model rationale: _{c(a.rationale)}_")
            w(f"  - Imagery: {im.provider} `{im.image_id}`, captured {im.captured_at or 'unknown'}, {im.distance_m} m from point, heading {im.heading_deg}°; "
              f"SHA-256 `{(im.image_sha256 or '')[:16]}…`; {'archived in evidence folder' if im.stored_locally else 'not stored (provider terms)'}; {c(im.license_note)}")
            if im.request_url:
                w(f"  - Reference: `{c(im.request_url)}`")
        else:
            w(f"- **Street-level vision**: not available ({c(v.error) or 'not run'}).")
        w("")

        w("## 8. Exclusion Screening (HHS-OIG LEIE)\n")
        L = enr.leie
        if L.performed:
            w(f"Screened {len(L.subjects_screened)} subject(s) against {L.dataset_rows:,} active exclusions (file `{c(L.dataset_path) or 'in-memory'}`, SHA-256 `{(L.dataset_sha256 or '')[:16]}…`).\n")
            if L.matches:
                w("| Subject | Role | Strength | Matched on | LEIE row | Excl. type | Excl. date |\n|---|---|---|---|---:|---|---|")
                for m in L.matches:
                    w(f"| {c(m.subject)} | {c(m.subject_role)} | {m.strength.value} | {', '.join(m.matched_on)} | {m.leie_row_number} | {c(m.exclusion_type)} | {m.exclusion_date or 'n/a'} |")
                w("")
            else:
                w("_No matches._\n")
        else:
            w(f"_Not screened: {c(L.error) or 'no LEIE file supplied'}._\n")

        w("## 9. Network Summary\n")
        n = d.network
        w(f"Connected component: **{len(n.component_entities)}** awardee(s), {_money(n.component_total_awarded)} total awarded in scope. "
          "Graphviz DOT of the component is in the JSON dossier (`network.dot`).\n")
        for sa in n.shared_addresses:
            w(f"- Shared address `{c(sa['address'])}`: {c('; '.join(sa['entities']))}")
        for sp in n.shared_people:
            w(f"- Shared person '{c(sp['person'])}': {c('; '.join(sp['entities']))}")
        if d.related_entities:
            w("\n| Related entity | UEI | Address | Total awarded | People | Linked by |\n|---|---|---|---:|---|---|")
            for r in d.related_entities:
                w(f"| {c(r['name'])} | {r['uei'] or 'n/a'} | {c(r['address'])} | {_money(r['total_awarded'])} | {c('; '.join(r['officers']))} | {', '.join(r['linked_by'])} |")
        w("")

        w("## 10. Relevant Statutory Frameworks\n")
        w("_Frameworks a reviewing authority may consider if, and only if, independent verification substantiates the "
          "observations. Listing a statute is not a legal conclusion. Consult qualified counsel._\n")
        for st in d.statutory_references:
            w(f"- **{st['name']}**, {st['citation']}: {st['relevance']}")
        w("")

        w("## 11. 2-Minute Physical Verification Checklist\n")
        w("_Fast triage, using public sources, of whether this lead deserves the full field work in section 12. It does not replace section 12._\n")
        for item in QUICK_PHYSICAL_CHECKLIST:
            w(f"- [ ] {item}")
        w("")

        w("## 12. Ground-Truthing Field Checklist\n")
        w("_Follow `docs/FIELD_INVESTIGATOR_SOP.md`. Public records and public right-of-way observation only._\n")
        for item in d.ground_truthing_checklist:
            w(f"- [ ] {item}")
        w("")

        w("## 13. Final Pre-Filing Review\n")
        w("_A short final check before anything is filed. It assumes section 12 is complete; it does not replace it._\n")
        for item in FINAL_REVIEW_CHECKLIST:
            w(f"- [ ] {item}")
        w("")

        w("## 14. Caveats & Limitations\n")
        for cv in d.caveats:
            w(f"- {cv}")
        w("")

        w("## 15. Chain of Custody\n")
        if d.evidence_manifest:
            w(f"Raw API responses are archived write-once under `{d.evidence_dir}`. `provenance.json` there records, for every "
              "call, the exact URL, request body, UTC timestamp, HTTP status and the SHA-256 of the response as received. "
              "`SHA256SUMS` covers every file, including this dossier.\n")
            w("| Artifact | Kind | Bytes | SHA-256 | Source URL | Retrieved (UTC) |\n|---|---|---:|---|---|---|")
            for art in d.evidence_manifest:
                w(f"| `{art.path}` | {art.kind} | {art.bytes:,} | `{art.sha256[:16]}…` | {c((art.source_url or '')[:90])} | {f'{art.retrieved_at:%Y-%m-%d %H:%M:%S}' if art.retrieved_at else ''} |")
            w("")
        w("| Record | Source | Request | HTTP | Retrieved (UTC) | Attempts | Response SHA-256 (prefix) |\n|---|---|---|---|---|---:|---|")
        for r in d.sources:
            w(f"| `{r.record_id}` | {r.source} | `{r.method} {c(r.url)}` | {r.status_code or c(r.error) or ''} | {r.retrieved_at:%Y-%m-%d %H:%M:%S} | {r.attempts} | `{(r.response_sha256 or '')[:16]}` |")
        w("")
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
