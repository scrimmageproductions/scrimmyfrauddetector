"""Vercel Python function: the JSON API behind the hosted Scrimmy console (public/index.html).

Every route is a short, stateless step (see scrimmy/service.py); the browser holds scan
state between calls. Set SCRIMMY_ACCESS_TOKEN in the Vercel project to require the
token on every request, so strangers cannot run scans on your deployment.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # make the scrimmy package importable on Vercel

from fastapi import Depends, FastAPI, Header, HTTPException  # noqa: E402
from fastapi.responses import Response  # noqa: E402
from pydantic import BaseModel  # noqa: E402

from scrimmy import __version__, service  # noqa: E402
from scrimmy.clients import SourceAPIError  # noqa: E402
from scrimmy.models import AwardeeEntity, EnrichmentData, SourceRecord  # noqa: E402

app = FastAPI(title="Scrimmy API", version=__version__, docs_url="/api/docs", openapi_url="/api/openapi.json")


def require_token(x_scrimmy_token: str | None = Header(default=None)) -> None:
    expected = os.environ.get("SCRIMMY_ACCESS_TOKEN")
    if expected and x_scrimmy_token != expected:
        raise HTTPException(status_code=401, detail="missing or invalid access token")


class EnrichBody(BaseModel):
    options: service.WebScanRequest
    entity: AwardeeEntity


class ScoreBody(BaseModel):
    options: service.WebScanRequest
    items: list[service.Item]
    records: list[SourceRecord] = []


class SiteBody(BaseModel):
    options: service.WebScanRequest
    entity: AwardeeEntity
    enrichment: EnrichmentData


class LeadBody(BaseModel):
    options: service.WebScanRequest
    items: list[service.Item]
    records: list[service.RecordBundle]
    entity_key: str


def _run(fn, *args):
    try:
        return fn(*args)
    except service.ServiceError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except SourceAPIError as exc:
        raise HTTPException(status_code=502, detail=f"upstream data source failed: {exc}") from exc
    except (ValueError, OSError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/health")
def health() -> dict:
    return {"ok": True, "version": __version__, **service.capabilities()}


@app.post("/api/search", dependencies=[Depends(require_token)])
def search(options: service.WebScanRequest) -> dict:
    return _run(service.search, options)


@app.post("/api/enrich", dependencies=[Depends(require_token)])
def enrich(body: EnrichBody) -> dict:
    return _run(service.enrich, body.options, body.entity)


@app.post("/api/score", dependencies=[Depends(require_token)])
def score(body: ScoreBody) -> dict:
    return _run(service.score, body.options, body.items, body.records)


@app.post("/api/site-check", dependencies=[Depends(require_token)])
def site_check(body: SiteBody) -> dict:
    return _run(service.site_check, body.options, body.entity, body.enrichment)


@app.post("/api/dossier", dependencies=[Depends(require_token)])
def dossier(body: LeadBody) -> dict:
    return _run(service.dossier, body.options, body.items, body.records, body.entity_key)


@app.post("/api/evidence", dependencies=[Depends(require_token)])
def evidence(body: LeadBody) -> Response:
    lead_id, data = _run(service.evidence_zip, body.options, body.items, body.records, body.entity_key)
    return Response(content=data, media_type="application/zip",
                    headers={"Content-Disposition": f'attachment; filename="{lead_id}_evidence.zip"'})
