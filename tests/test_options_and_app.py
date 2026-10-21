from datetime import date
from pathlib import Path

from scrimmy.options import ScanOptions, fiscal_year_bounds
from scrimmy.scorer import LEIE_EXCLUSION_MATCH, MATHEMATICAL_IMPOSSIBILITY


def test_fiscal_year():
    assert fiscal_year_bounds(2025) == (date(2024, 10, 1), date(2025, 9, 30))


def test_default_targets_priority_verticals():
    s = ScanOptions(state="MN").to_settings()
    assert set(s.filters.naics_codes) == {"624410", "624210", "621610", "623110", "624120"}
    assert s.filters.min_award == 250_000


def test_profiles_and_validation():
    s = ScanOptions(state="MN", vertical="hospice", scoring_profile="capacity", fiscal_year=2024).to_settings()
    assert s.scoring.weights[MATHEMATICAL_IMPOSSIBILITY] == 40 and s.scoring.weights[LEIE_EXCLUSION_MATCH] == 35
    assert s.filters.start_date == date(2023, 10, 1)
    assert ScanOptions().validate()  # unbounded scan rejected
    assert ScanOptions(strike_force="custom").validate()
    assert not ScanOptions(nationwide=True).validate()


def test_strike_force_filters_use_county_fips():
    s = ScanOptions(strike_force="florida").to_settings()
    locs = s.filters.to_api("grants")["recipient_locations"]
    assert {"country": "USA", "state": "FL", "county": "086"} in locs  # Miami-Dade
    assert s.strike_force.label == "Florida Strike Force"
    west = ScanOptions(strike_force="west_coast").to_settings().filters.to_api("contracts")["recipient_locations"]
    assert {"country": "USA", "state": "AZ"} in west and {"country": "USA", "state": "CA", "county": "037"} in west


def test_assign_strike_force_district():
    from scrimmy.models import Address, AwardeeEntity, EnrichmentData, GeocodeResult
    from scrimmy.pipeline import STRIKE_FORCE_PRESETS, assign_strike_force

    def place(state, county_fips=None, county_name=None):
        e = AwardeeEntity(entity_key="x", name="X", normalized_name="X", address=Address(state=state, county=county_name))
        d = EnrichmentData(geocode=GeocodeResult(performed=True, matched=bool(county_fips), county_fips=county_fips))
        return e, d

    e, d = place("LA", "22033")  # East Baton Rouge Parish
    assign_strike_force(e, d, STRIKE_FORCE_PRESETS["gulf_coast"])
    assert d.strike_force.district == "Middle District of Louisiana" and d.strike_force.in_focus_area
    e, d = place("NJ")  # single-district state
    assign_strike_force(e, d, STRIKE_FORCE_PRESETS["northeast"])
    assert d.strike_force.us_attorney_office == "U.S. Attorney's Office, District of New Jersey"
    e, d = place("TX", None, "HARRIS")  # falls back to the USASpending county name
    assign_strike_force(e, d, STRIKE_FORCE_PRESETS["texas"])
    assert d.strike_force.district == "Southern District of Texas"
    e, d = place("TX", "48029")  # Bexar: not a focus county
    assign_strike_force(e, d, STRIKE_FORCE_PRESETS["texas"])
    assert not d.strike_force.in_focus_area and "uscourts.gov" in d.strike_force.basis


def test_app_renders_ready_state_without_network():
    from streamlit.testing.v1 import AppTest

    at = AppTest.from_file(str(Path(__file__).parents[1] / "streamlit_app" / "app.py"), default_timeout=60)
    at.run()
    assert not at.exception
    assert any("Ready to scan" in m.value for m in at.markdown)
    assert any(b.label == "Run live scan" for b in at.sidebar.button)
