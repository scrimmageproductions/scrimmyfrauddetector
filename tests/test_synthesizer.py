from dataclasses import dataclass

from scrimmy.synthesizer import LanguageGuard, Synthesizer, make_backend

FACTS = {
    "entity_name": "ALPHA CARE LLC", "uei": "EXMPL1", "address_of_record": "1 EXAMPLE ST, X, MN 55990",
    "award_count": 2, "total_awarded": "$1,550,000.00", "awarding_agencies": ["Department of Veterans Affairs"],
    "score": 95, "score_max": 100,
    "triggered_indicators": [{"title": "Newly Formed Windfall", "points": 30, "observation": "56 days after formation"}],
    "indicators_with_insufficient_data": [], "premises_classification": "residential",
}


@dataclass
class FakeLLM:
    text: str
    name: str = "fake"
    model: str = "fake-1"

    def complete(self, system, prompt):
        return self.text


def test_template_summary_passes_guard():
    s = Synthesizer()
    text, prov = s.narrative(FACTS)
    assert prov.backend == "template"
    assert LanguageGuard().violations(text, FACTS) == []


def test_accusatory_llm_output_falls_back():
    s = Synthesizer(FakeLLM("ALPHA CARE LLC is a fraud and stole $1,550,000.00 from taxpayers."))
    text, prov = s.narrative(FACTS)
    assert prov.fell_back_to_template and "LanguageGuard" in prov.fallback_reason
    assert "is a fraud" not in text


def test_invented_numbers_fall_back():
    s = Synthesizer(FakeLLM("The recipient received roughly $2.3 million, an unverified physical discrepancy."))
    _, prov = s.narrative(FACTS)
    assert prov.fell_back_to_template and "2.3" in prov.fallback_reason


def test_clean_llm_output_is_used():
    clean = ("ALPHA CARE LLC holds 2 awards totaling $1,550,000.00. The screen scored 95 of 100. "
             "These are unverified discrepancies that require independent verification and may have lawful explanations.")
    text, prov = Synthesizer(FakeLLM(clean)).narrative(FACTS)
    assert text == clean and not prov.fell_back_to_template and prov.model == "fake-1"


def test_backend_errors_fall_back():
    class Boom(FakeLLM):
        def complete(self, system, prompt):
            raise RuntimeError("no network")

    _, prov = Synthesizer(Boom("")).narrative(FACTS)
    assert prov.fell_back_to_template and "no network" in prov.fallback_reason


def test_auto_backend_without_keys_is_template(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    assert make_backend("auto").name == "template"
