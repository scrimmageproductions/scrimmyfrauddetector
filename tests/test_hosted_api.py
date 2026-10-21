"""The Vercel deployment: stateless API steps driven the way public/app.js drives them."""

import io
import json
import hashlib
import re
import zipfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from scrimmy import service
from fixtures.api_fixtures import fixture_transport

ROOT = Path(__file__).parents[1]


@pytest.fixture
def client(monkeypatch):
    from scrimmy.clients import HostRateLimiter

    monkeypatch.setattr(service, "TRANSPORT_FACTORY", fixture_transport)
    monkeypatch.setattr(service, "LIMITER_FACTORY", lambda: HostRateLimiter({}, default_interval=0.0))
    monkeypatch.delenv("SCRIMMY_ACCESS_TOKEN", raising=False)
    from api.index import app

    return TestClient(app)


OPTS = {"state": "MN", "verticals": ["home_health"], "min_award": 50000, "max_awards": 50}


def run_scan(client):
    found = client.post("/api/search", json=OPTS).json()
    records = {b["record"]["record_id"]: b for b in found["records"]}
    items = []
    for e in found["entities"]:
        r = client.post("/api/enrich", json={"options": OPTS, "entity": e}).json()
        items.append({"entity": r["entity"], "enrichment": r["enrichment"]})
        records.update({b["record"]["record_id"]: b for b in r["records"]})
    slim = [{**b["record"], "raw_snippet": None, "request_body": None} for b in records.values()]
    scored = client.post("/api/score", json={"options": OPTS, "items": items, "records": slim}).json()
    return found, items, records, scored


def test_full_hosted_scan(client):
    found, items, records, scored = run_scan(client)
    assert len(found["entities"]) == 8 and not found["all_failed"]
    assert len(records) == len(set(records))  # namespaced record ids never collide across requests
    assert len({rid.split("-")[1] for rid in records}) > 1
    top = scored["results"][0]
    assert top["score"]["score"] >= 50 and set(scored["network"]) == {i["entity"]["entity_key"] for i in items}

    body = {"options": OPTS, "items": items, "records": list(records.values()), "entity_key": top["entity_key"]}
    d = client.post("/api/dossier", json=body).json()
    assert d["markdown"].startswith("# Investigative Lead Briefing") and d["dossier"]["score"]["score"] == top["score"]["score"]

    z = client.post("/api/evidence", json=body)
    assert z.status_code == 200 and z.headers["content-type"] == "application/zip"
    zf = zipfile.ZipFile(io.BytesIO(z.content))
    names = zf.namelist()
    lead = names[0].split("/")[0]
    sums = zf.read(f"{lead}/SHA256SUMS").decode().splitlines()
    for line in sums:  # every archived file matches its recorded hash
        digest, _, rel = line.partition("  ")
        assert hashlib.sha256(zf.read(f"{lead}/{rel}")).hexdigest() == digest
    assert any(n.startswith(f"{lead}/api/") for n in names) and f"{lead}/dossier.md" in names


def test_errors_are_clean_json(client):
    r = client.post("/api/search", json={**OPTS, "verticals": ["nope"]})
    assert r.status_code == 400 and "unknown vertical" in r.json()["detail"]
    r = client.post("/api/search", json={"verticals": ["childcare"]})
    assert r.status_code == 400 and "bound the scan" in r.json()["detail"]
    assert client.post("/api/search", json={**OPTS, "max_awards": 5000}).status_code == 422


def test_access_token(client, monkeypatch):
    monkeypatch.setenv("SCRIMMY_ACCESS_TOKEN", "s3cret")
    assert client.get("/api/health").json()["access_token_required"] is True
    assert client.post("/api/search", json=OPTS).status_code == 401
    assert client.post("/api/search", json=OPTS, headers={"X-Scrimmy-Token": "s3cret"}).status_code == 200


def test_vercel_layout_is_consistent():
    cfg = json.loads((ROOT / "vercel.json").read_text())
    assert cfg["outputDirectory"] == "public" and "api/index.py" in cfg["functions"]
    assert not (ROOT / "app.py").exists()  # a root app.py would be auto-detected as the Python entrypoint
    html = (ROOT / "public" / "index.html").read_text()
    for ref in re.findall(r'(?:src|href)="((?:vendor/)?[\w./-]+\.(?:js|css))"', html):
        assert (ROOT / "public" / ref).exists(), ref
    reqs = (ROOT / "requirements.txt").read_text()
    assert "fastapi" in reqs and "streamlit" not in reqs  # keep the function bundle small
