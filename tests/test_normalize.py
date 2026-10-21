import pytest

from scrimmy.models import Address
from scrimmy.normalize import names_related, normalize_address, normalize_entity_name, normalize_person_name, normalize_zip


@pytest.mark.parametrize("raw,expected", [
    ("The Acme Widget Co., L.L.C.", "ACME WIDGET"),
    ("ACME WIDGET COMPANY INC dba Acme Kids", "ACME WIDGET"),
    ("Smith & Jones, P.L.L.C.", "SMITH JONES"),
    ("Bright Futures Learning Center, Incorporated", "BRIGHT FUTURES LEARNING CENTER"),
    ("Services Inc", "SERVICES"),
])
def test_entity_names(raw, expected):
    assert normalize_entity_name(raw) == expected


def test_person_names_are_order_and_title_insensitive():
    assert normalize_person_name("Dr. Jane Q. Sample, MD") == normalize_person_name("SAMPLE, JANE") == "JANE SAMPLE"
    assert normalize_person_name("John Smith Jr.") == "JOHN SMITH"


def _key(line1, line2=None, zip5="55401"):
    return normalize_address(Address(line1=line1, line2=line2, city="Minneapolis", state="mn", zip5=zip5))


def test_address_variants_share_a_location_key():
    keys = {_key("123 North Main Street", "Suite 200").location_key, _key("123 N. Main St.", "#200").location_key,
            _key("123 N MAIN ST UNIT 200").location_key, _key("123 north main st ste. 200").location_key}
    assert keys == {"123 N MAIN ST UNIT 200|55401"}


def test_only_trailing_street_type_is_abbreviated():
    assert _key("4500 Lake Avenue").normalized_street == "4500 LAKE AVE"
    assert _key("12 Park Avenue South").normalized_street == "12 PARK AVE S"


def test_po_box_and_pmb_detection():
    po = _key("P.O. Box 55")
    assert po.is_po_box and po.normalized_street == "PO BOX 55"
    assert not _key("100 Box Elder Rd").is_po_box
    pmb = _key("4500 Lake Ave", "PMB 118")
    assert pmb.is_pmb and pmb.normalized_unit == "PMB 118"


def test_zip_normalization():
    assert normalize_zip("55401-1234") == ("55401", "1234")
    assert normalize_zip("554011234") == ("55401", "1234")
    assert normalize_zip("2134") == ("02134", None)
    assert normalize_zip("abc") == (None, None)


def test_names_related():
    assert names_related("ACME HOME CARE", "ACME HOME")
    assert names_related("NORTHSTAR LEARNING", "NORTH STAR LEARNING")
    assert not names_related("RIVERBEND CARE", "SUMMIT WELLNESS")
