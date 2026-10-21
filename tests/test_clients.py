import hashlib
import json

import httpx
import pytest

from scrimmy.clients import AwardSearchFilters, SAMEntityClient, SourceAPIError, USASpendingClient


def test_retries_then_succeeds_and_records_provenance(make_http):
    calls = {"n": 0}

    def handler(req):
        calls["n"] += 1
        if calls["n"] < 3:
            return httpx.Response(503, json={"detail": "busy"})
        return httpx.Response(200, content=b'{"ok": true}', headers={"Content-Type": "application/json"})

    http = make_http(httpx.MockTransport(handler))
    payload, rec = http.request_json("test", "GET", "https://example.test/x", params={"a": "1", "b": None})
    assert payload == {"ok": True}
    assert rec.attempts == 3 and len(http.sleeps) == 2
    assert http.sleeps[1] >= http.sleeps[0] * 1.5  # exponential backoff (jitter <= 0.5)
    assert rec.url == "https://example.test/x?a=1"
    assert rec.response_sha256 == hashlib.sha256(b'{"ok": true}').hexdigest()
    assert rec.raw_snippet == {"ok": True} and rec.status_code == 200
    assert http.ledger.get(rec.record_id) is rec


def test_honours_retry_after(make_http):
    responses = iter([httpx.Response(429, headers={"Retry-After": "7"}), httpx.Response(200, json={})])
    http = make_http(httpx.MockTransport(lambda req: next(responses)))
    http.request_json("test", "GET", "https://example.test/")
    assert http.sleeps == [7.0]


def test_gives_up_and_records_failure(make_http):
    http = make_http(httpx.MockTransport(lambda req: httpx.Response(500, json={"e": 1})), max_attempts=3)
    with pytest.raises(SourceAPIError) as exc:
        http.request_json("test", "GET", "https://example.test/")
    assert exc.value.record.attempts == 3 and exc.value.record.error == "HTTP 500"
    assert len(http.ledger) == 1


def test_transport_errors_are_retried(make_http):
    state = {"n": 0}

    def handler(req):
        state["n"] += 1
        if state["n"] == 1:
            raise httpx.ConnectError("boom")
        return httpx.Response(200, json={"ok": 1})

    http = make_http(httpx.MockTransport(handler))
    _, rec = http.request_json("test", "GET", "https://example.test/")
    assert rec.attempts == 2


def test_client_errors_are_not_retried(make_http):
    http = make_http(httpx.MockTransport(lambda req: httpx.Response(400, json={"detail": "bad filter"})))
    with pytest.raises(SourceAPIError):
        http.request_json("test", "GET", "https://example.test/")
    assert http.sleeps == []


def test_sam_api_key_is_redacted_from_evidence(make_http):
    seen = {}

    def handler(req):
        seen["url"] = str(req.url)
        return httpx.Response(200, json={"entityData": []})

    http = make_http(httpx.MockTransport(handler))
    _, rec = SAMEntityClient(http, "SECRET123").get_entity("EXMPL0000001")
    assert "SECRET123" in seen["url"]
    assert "SECRET123" not in rec.url and "api_key=%3Credacted%3E" in rec.url


def test_filters_never_mix_contract_and_assistance_codes():
    f = AwardSearchFilters(state="mn", naics_codes=["624410"], program_numbers=["93.575"], min_award=250_000)
    contracts, grants = f.to_api("contracts"), f.to_api("grants")
    assert contracts["naics_codes"] == {"require": ["624410"]} and "program_numbers" not in contracts
    assert grants["program_numbers"] == ["93.575"] and "naics_codes" not in grants
    assert contracts["recipient_locations"] == [{"country": "USA", "state": "MN"}]
    assert contracts["award_amounts"] == [{"lower_bound": 250_000}]
    assert contracts["award_type_codes"] == ["A", "B", "C", "D"]


def test_award_pagination(make_http):
    def handler(req):
        body = json.loads(req.content)
        page = body["page"]
        rows = [{"Award ID": f"P{page}-{i}"} for i in range(body["limit"])]
        return httpx.Response(200, json={"results": rows, "page_metadata": {"hasNext": page < 5}})

    client = USASpendingClient(make_http(httpx.MockTransport(handler)))
    rows = list(client.iter_awards(AwardSearchFilters(state="MN"), "grants", 250))
    assert len(rows) == 250 and rows[-1][0]["Award ID"].startswith("P3-")
