from scrimmy.enrichment import classify_land_use
from scrimmy.models import Address, LandUseClass
from scrimmy.normalize import normalize_address

LAT, LON = 45.0, -93.0


def addr(line1="10 Example St", line2=None):
    return normalize_address(Address(line1=line1, line2=line2, city="X", state="MN", zip5="55990"))


def way(tags, dlat=0.00005):
    return {"type": "way", "id": 1, "center": {"lat": LAT + dlat, "lon": LON}, "tags": tags}


def node(tags, dlat=0.00005):
    return {"type": "node", "id": 2, "lat": LAT + dlat, "lon": LON, "tags": tags}


def landuse(value):
    return {"type": "way", "id": 3, "tags": {"landuse": value}}


def test_house_without_pois_is_residential():
    r = classify_land_use(addr(), LAT, LON, [way({"building": "house"}), landuse("residential")], 40)
    assert r.classification is LandUseClass.RESIDENTIAL
    assert r.nearest_building_tag == "house"


def test_house_with_shop_is_mixed():
    r = classify_land_use(addr(), LAT, LON, [way({"building": "house"}), node({"shop": "bakery"})], 40)
    assert r.classification is LandUseClass.MIXED


def test_suite_next_to_ups_store_is_cmra():
    els = [way({"building": "retail"}), node({"shop": "shipping", "name": "The UPS Store"})]
    r = classify_land_use(addr(line2="Suite 210"), LAT, LON, els, 40)
    assert r.classification is LandUseClass.CMRA_MAIL_DROP
    # Same storefront but no unit -> informational only
    r2 = classify_land_use(addr(), LAT, LON, els, 40)
    assert r2.classification is LandUseClass.COMMERCIAL


def test_pmb_and_po_box_short_circuit():
    assert classify_land_use(addr(line2="PMB 9"), LAT, LON, [], 40).classification is LandUseClass.CMRA_MAIL_DROP
    assert classify_land_use(addr("PO Box 7"), LAT, LON, [], 40).classification is LandUseClass.PO_BOX


def test_nothing_mapped_and_undeveloped():
    assert classify_land_use(addr(), LAT, LON, [], 40).classification is LandUseClass.NO_STRUCTURE_MAPPED
    r = classify_land_use(addr(), LAT, LON, [landuse("brownfield")], 40)
    assert r.classification is LandUseClass.NO_STRUCTURE_MAPPED


def test_generic_building_uses_landuse():
    assert classify_land_use(addr(), LAT, LON, [way({"building": "yes"}), landuse("residential")], 40).classification is LandUseClass.RESIDENTIAL
    assert classify_land_use(addr(), LAT, LON, [way({"building": "yes"}), landuse("commercial")], 40).classification is LandUseClass.COMMERCIAL
    assert classify_land_use(addr(), LAT, LON, [way({"building": "yes"})], 40).classification is LandUseClass.UNKNOWN


def test_non_business_amenities_ignored():
    r = classify_land_use(addr(), LAT, LON, [way({"building": "house"}), node({"amenity": "bench"})], 40)
    assert r.classification is LandUseClass.RESIDENTIAL
