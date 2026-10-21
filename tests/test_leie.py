import pytest

from scrimmy.leie_matcher import LEIEMatcher
from scrimmy.models import Address, AwardeeEntity, LEIEMatchStrength, Officer
from scrimmy.normalize import normalize_address, normalize_entity_name, normalize_person_name

HEADER = "LASTNAME,FIRSTNAME,MIDNAME,BUSNAME,GENERAL,SPECIALTY,UPIN,NPI,DOB,ADDRESS,CITY,STATE,ZIP,EXCLTYPE,EXCLDATE,REINDATE,WAIVERDATE,WVRSTATE\n"
ROWS = (
    "SAMPLE,JORDAN,A,,IND,NURSE,,0000000000,19700101,1 X ST,LAKEVIEW FALLS,MN,55990,1128b4,20220315,00000000,00000000,\n"
    "SAMPLE,JORDAN,,,IND,NURSE,,0000000000,19700101,1 X ST,DULUTH,MN,55802,1128b4,20220315,00000000,00000000,\n"
    ",,,EXAMPLE BAD CARE LLC,BUS,HOME HEALTH,,1234567890,,2 Y ST,LAKEVIEW FALLS,MN,55990,1128a1,20210101,00000000,00000000,\n"
    "OLD,REINSTATED,,,IND,,,,,,X,MN,55990,1128b4,20100101,20150101,00000000,\n"
)


@pytest.fixture
def matcher():
    return LEIEMatcher.from_bytes((HEADER + ROWS).encode())


def test_reinstated_rows_are_dropped(matcher):
    assert len(matcher.rows) == 3 and matcher.sha256


def test_person_strength(matcher):
    strong = matcher.match_person("Jordan A. Sample", "officer", "MN", "Lakeview Falls", "55990", "A")
    assert {m.strength for m in strong} == {LEIEMatchStrength.STRONG, LEIEMatchStrength.POSSIBLE}
    elsewhere = matcher.match_person("Jordan Sample", "officer", "MN", "Rochester", "55901")
    assert {m.strength for m in elsewhere} == {LEIEMatchStrength.POSSIBLE}
    assert matcher.match_person("Jordan Sample", "officer", "WI", None, None) == []


def test_business_and_npi(matcher):
    biz = matcher.match_business("Example Bad Care, L.L.C.", "MN", "LAKEVIEW FALLS", "55990")
    assert biz[0].strength is LEIEMatchStrength.STRONG and biz[0].leie_row["BUSNAME"] == "EXAMPLE BAD CARE LLC"
    assert matcher.match_npi("1234567890", "x")[0].strength is LEIEMatchStrength.CONFIRMED_IDENTIFIER
    assert matcher.match_npi("0000000000", "x") == []  # zero placeholder NPIs never match


def test_screen_entity(matcher):
    e = AwardeeEntity(entity_key="A", name="Alpha Care LLC", normalized_name=normalize_entity_name("Alpha Care LLC"),
                      address=normalize_address(Address(line1="1 A St", city="Lakeview Falls", state="MN", zip5="55990")),
                      officers=[Officer(name="Jordan A. Sample", normalized_name=normalize_person_name("Jordan A. Sample"), role_source="t")])
    res = matcher.screen(e, npis=["1234567890"])
    assert res.performed and res.dataset_rows == 3
    strengths = sorted(m.strength.value for m in res.matches)
    assert strengths == ["confirmed_identifier", "possible", "strong"]


def test_missing_columns_rejected():
    with pytest.raises(ValueError, match="missing required"):
        LEIEMatcher.from_bytes(b"A,B\n1,2\n")
