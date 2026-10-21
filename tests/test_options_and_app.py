from datetime import date
from pathlib import Path

from scrimmy.options import ScanOptions, fiscal_year_bounds
from scrimmy.scorer import LEIE_EXCLUSION_MATCH, MATHEMATICAL_IMPOSSIBILITY


def test_fiscal_year():
    assert fiscal_year_bounds(2025) == (date(2024, 10, 1), date(2025, 9, 30))


def test_default_targets_priority_verticals():
    s = ScanOptions(state="MN").to_settings()
    assert {"624410", "624210", "621610", "623110", "624120", "621498", "621999"} <= set(s.filters.naics_codes)
    assert s.filters.min_award == 250_000


def test_profiles_and_validation():
    s = ScanOptions(state="MN", vertical="hospice", scoring_profile="capacity", fiscal_year=2024).to_settings()
    assert s.scoring.weights[MATHEMATICAL_IMPOSSIBILITY] == 40 and s.scoring.weights[LEIE_EXCLUSION_MATCH] == 35
    assert s.filters.start_date == date(2023, 10, 1)
    assert ScanOptions().validate()  # unbounded scan rejected


def test_app_renders_ready_state_without_network():
    from streamlit.testing.v1 import AppTest

    at = AppTest.from_file(str(Path(__file__).parents[1] / "streamlit_app" / "app.py"), default_timeout=60)
    at.run()
    assert not at.exception
    assert any("Ready to scan" in m.value for m in at.markdown)
    assert any(b.label == "Run live scan" for b in at.sidebar.button)
