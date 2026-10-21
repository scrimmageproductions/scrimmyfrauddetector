import json
from dataclasses import dataclass
from datetime import date

import httpx
import pytest

from scrimmy.clients import GoogleStreetViewClient, MapillaryClient
from scrimmy.models import Address, AwardeeEntity, AwardRecord, EnrichmentData, GeocodeResult, LandUseClass, LandUseResult, Officer, RuleStatus, VisionCategory
from scrimmy.network_graph import EntityGraph
from scrimmy.normalize import normalize_address, normalize_entity_name, normalize_person_name
from scrimmy.scorer import PHYSICAL_SITE_MISMATCH, DeterministicScorer, ScoringConfig, ScoringContext
from scrimmy.vision_verifier import VisionVerifier, bearing_deg, parse_assessment


def ent(key, name, line1="1 Example St", officers=(), naics="624410", amount=600_000):
    return AwardeeEntity(
        entity_key=key, name=name, normalized_name=normalize_entity_name(name),
        address=normalize_address(Address(line1=line1, city="X", state="MN", zip5="55990")),
        officers=[Officer(name=o, normalized_name=normalize_person_name(o), role_source="t") for o in officers],
        awards=[AwardRecord(award_id=key, award_group="contracts", amount=amount, date_signed=date(2025, 3, 1), naics_code=naics)],
    )


def test_graph_serial_and_commercial_agents_excluded():
    a = ent("A", "Alpha Care", officers=["Pat Example", "CT Corporation System"])
    b = ent("B", "Bravo Kids", "2 B St", officers=["PAT EXAMPLE", "CT Corporation System"])
    c = ent("C", "Charlie Learning", "3 C St", officers=["Example, Pat"])
    g = EntityGraph.build([a, b, c])
    assert g.shared_people("A") == {"EXAMPLE PAT": ["B", "C"]}  # registered-agent service is not a person node
    summary = g.summary("A")
    assert len(summary.component_entities) == 3 and summary.shared_people[0]["entities"] == ["Alpha Care", "Bravo Kids", "Charlie Learning"]
    assert '\\n$600,000' in summary.dot


def test_serial_threshold_differs_by_profile():
    a = ent("A", "Alpha Care", officers=["Pat Example"])
    b = ent("B", "Bravo Kids", "2 B St", officers=["Pat Example"])
    pairs = [(a, EnrichmentData()), (b, EnrichmentData())]
    ctx = ScoringContext.build(pairs)
    reg = DeterministicScorer(ScoringConfig.for_profile("registry")).score(a, pairs[0][1], ctx)
    cap = DeterministicScorer(ScoringConfig.for_profile("capacity")).score(a, pairs[0][1], ctx)
    status = lambda bd: next(e for e in bd.evaluations if e.rule_id == "EXECUTIVE_SERIAL_REGISTRATION").status  # noqa: E731
    assert status(reg) is RuleStatus.TRIGGERED and status(cap) is RuleStatus.NOT_TRIGGERED  # capacity needs 3 entities


def test_parse_assessment():
    a = parse_assessment('Sure:\n{"category": "single_family_residence", "confidence": 0.9, "physical_markers": ["driveway"], "rationale": "A house."}')
    assert a.category is VisionCategory.SINGLE_FAMILY_RESIDENCE and a.confidence == 0.9
    for bad in ("no json", '{"category": "CASTLE", "confidence": 0.5}', '{"category": "VACANT_LOT", "confidence": 3}'):
        with pytest.raises(ValueError):
            parse_assessment(bad)


def test_bearing():
    assert round(bearing_deg(0, 0, 1, 0)) == 0 and round(bearing_deg(0, 0, 0, 1)) == 90


@dataclass
class FakeVision:
    reply: str
    name: str = "fake"
    model: str = "v1"

    def classify(self, image, media_type, context):
        assert media_type == "image/jpeg" and image.startswith(b"\xff\xd8\xff")
        return self.reply


JPEG = b"\xff\xd8\xff\xe0" + b"0" * 64
GEO = GeocodeResult(performed=True, matched=True, matched_address="1 EXAMPLE ST", latitude=45.0, longitude=-93.0)


def mapillary_handler(req):
    if req.url.host == "graph.mapillary.com":
        return httpx.Response(200, json={"data": [{"id": "111", "captured_at": 1717200000000, "thumb_1024_url": "https://img.example.test/111.jpg",
                                                   "computed_geometry": {"type": "Point", "coordinates": [-93.0001, 45.0001]}}]})
    return httpx.Response(200, content=JPEG, headers={"Content-Type": "image/jpeg"})


def site_score(verifier, land=LandUseClass.RESIDENTIAL, naics="624410"):
    e = ent("A", "Alpha Care", naics=naics)
    d = EnrichmentData(geocode=GEO, land_use=LandUseResult(performed=True, classification=land))
    d.vision = verifier.verify(GEO, e.name)
    ctx = ScoringContext.build([(e, d)])
    bd = DeterministicScorer(ScoringConfig.for_profile("capacity")).score(e, d, ctx)
    return d, next(ev for ev in bd.evaluations if ev.rule_id == PHYSICAL_SITE_MISMATCH)


def test_vision_mapillary_triggers_site_mismatch(make_http):
    http = make_http(httpx.MockTransport(mapillary_handler))
    reply = json.dumps({"category": "SINGLE_FAMILY_RESIDENCE", "confidence": 0.92, "physical_markers": ["driveway"], "rationale": "House."})
    d, ev = site_score(VisionVerifier(FakeVision(reply), mapillary=MapillaryClient(http, "TOKEN")))
    assert d.vision.imagery.stored_locally and d.vision.imagery.captured_at == date(2024, 6, 1)
    assert "TOKEN" not in http.ledger.all()[0].url
    assert ev.status is RuleStatus.TRIGGERED and ev.points_awarded == 25


def test_vision_guards(make_http):
    http = make_http(httpx.MockTransport(mapillary_handler))
    low = json.dumps({"category": "VACANT_LOT", "confidence": 0.5, "physical_markers": [], "rationale": "?"})
    assert site_score(VisionVerifier(FakeVision(low), mapillary=MapillaryClient(http, "T")))[1].status is RuleStatus.INSUFFICIENT_DATA
    house = json.dumps({"category": "SINGLE_FAMILY_RESIDENCE", "confidence": 0.95, "physical_markers": [], "rationale": "."})
    v = VisionVerifier(FakeVision(house), mapillary=MapillaryClient(http, "T"))
    assert site_score(v, land=LandUseClass.COMMERCIAL)[1].status is RuleStatus.NOT_TRIGGERED       # conflicting OSM evidence
    assert site_score(v, naics="621610")[1].status is RuleStatus.NOT_APPLICABLE                  # home health: off-site services
    garbage = VisionVerifier(FakeVision("I cannot tell"), mapillary=MapillaryClient(http, "T"))
    assert site_score(garbage)[1].status is RuleStatus.INSUFFICIENT_DATA


def test_google_imagery_is_hashed_not_stored(make_http, tmp_path):
    def handler(req):
        if req.url.path.endswith("/metadata"):
            return httpx.Response(200, json={"status": "OK", "pano_id": "PANO1", "date": "2024-05", "location": {"lat": 45.0002, "lng": -93.0}})
        return httpx.Response(200, content=JPEG, headers={"Content-Type": "image/jpeg"})

    http = make_http(httpx.MockTransport(handler))
    reply = json.dumps({"category": "COMMERCIAL_FACILITY_WITH_SIGNAGE", "confidence": 0.9, "physical_markers": ["signage"], "rationale": "."})
    res = VisionVerifier(FakeVision(reply), google=GoogleStreetViewClient(http, "GKEY")).verify(GEO, "x")
    assert res.imagery.image_id == "PANO1" and not res.imagery.stored_locally and res.imagery.image_sha256
    assert all("GKEY" not in r.url for r in http.ledger.all())
    from scrimmy.evidence import EvidenceVault

    arts = EvidenceVault(tmp_path).archive("L1", {r.record_id for r in http.ledger.all()}, http.ledger)
    assert not any(a.kind == "image" for a in arts)  # Google imagery never written to disk
