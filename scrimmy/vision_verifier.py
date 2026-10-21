"""Street-level imagery retrieval and vision-model site classification.

Imagery providers (both optional; neither is "zero-key"):

* Mapillary (``MAPILLARY_TOKEN``): CC BY-SA 4.0 imagery that may be archived with
  attribution, so the image is written to the lead's evidence directory.
* Google Street View Static API (``GOOGLE_MAPS_API_KEY``): Google Maps Platform terms
  restrict storing or caching content. Scrimmy classifies the image in memory and
  keeps only the pano_id, capture date, request URL (key redacted) and the SHA-256
  of the bytes as fetched. A reviewer can re-open the same panorama by pano_id.

The vision model only *describes* the image, as a category, a self-reported confidence
and the physical markers it saw. Whether that description counts toward the score
is decided by a deterministic rule in ``scorer.py`` with fixed thresholds.
"""

from __future__ import annotations

import base64
import json
import logging
import math
import os
import re
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Protocol

import httpx
from pydantic import ValidationError

from .clients import GoogleStreetViewClient, MapillaryClient, SourceAPIError
from .enrichment import haversine_m
from .models import GeocodeResult, ImageryRecord, VisionAssessment, VisionVerification

log = logging.getLogger(__name__)

VISION_SYSTEM_PROMPT = """You classify the building or land visible in a street-level photograph for a property-records review.
Return ONLY a JSON object with exactly these keys:
  "category": one of SINGLE_FAMILY_RESIDENCE, COMMERCIAL_FACILITY_WITH_SIGNAGE, COMMERCIAL_MAIL_RECEIVER_OR_PO_BOX,
              VACANT_LOT, INDUSTRIAL_WAREHOUSE, INDETERMINATE
  "confidence": number from 0.0 to 1.0
  "physical_markers": list of short strings naming visible physical features (e.g. "residential driveway",
              "business signage", "playground equipment", "accessibility ramp", "commercial parking lot",
              "shipping-store storefront", "fleet vehicles", "loading dock", "undeveloped lot")
  "rationale": one or two sentences describing only what is visible
Rules: describe structures only. Do not identify, describe or count people, faces, children or license plates.
Do not speculate about the occupants, their business, or any wrongdoing. Use INDETERMINATE when the view is
obstructed, blurry, does not face a building, or is otherwise unclear."""


def bearing_deg(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dl = math.radians(lon2 - lon1)
    x = math.sin(dl) * math.cos(p2)
    y = math.cos(p1) * math.sin(p2) - math.sin(p1) * math.cos(p2) * math.cos(dl)
    return (math.degrees(math.atan2(x, y)) + 360) % 360


def sniff_media_type(data: bytes, declared: str | None = None) -> str:
    if data[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return declared or "image/jpeg"


def parse_assessment(text: str) -> VisionAssessment:
    """Extract and strictly validate the model's JSON. Raises ValueError on anything malformed."""
    match = re.search(r"\{.*\}", text, re.S)
    if not match:
        raise ValueError("no JSON object in model output")
    try:
        data = json.loads(match.group(0))
        data["category"] = str(data.get("category", "")).strip().upper()
        return VisionAssessment.model_validate(data)
    except (json.JSONDecodeError, ValidationError, AttributeError) as exc:
        raise ValueError(f"invalid assessment JSON: {exc}") from exc


# --------------------------------------------------------------------------- #
# Vision model backends
# --------------------------------------------------------------------------- #


class VisionModel(Protocol):
    name: str
    model: str

    def classify(self, image: bytes, media_type: str, context: str) -> str: ...


@dataclass
class AnthropicVision:
    model: str
    name: str = "anthropic"

    def classify(self, image: bytes, media_type: str, context: str) -> str:
        import anthropic

        resp = anthropic.Anthropic().messages.create(
            model=self.model, max_tokens=600, system=VISION_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": [
                {"type": "image", "source": {"type": "base64", "media_type": media_type, "data": base64.b64encode(image).decode()}},
                {"type": "text", "text": context},
            ]}],
        )
        return "".join(getattr(b, "text", "") for b in resp.content)


@dataclass
class OpenAIVision:
    model: str
    name: str = "openai"

    def _messages(self, image: bytes, media_type: str, context: str) -> list[dict]:
        url = f"data:{media_type};base64,{base64.b64encode(image).decode()}"
        return [{"role": "system", "content": VISION_SYSTEM_PROMPT},
                {"role": "user", "content": [{"type": "text", "text": context}, {"type": "image_url", "image_url": {"url": url}}]}]

    def classify(self, image: bytes, media_type: str, context: str) -> str:
        import openai

        client = openai.OpenAI(base_url=os.environ.get("OPENAI_BASE_URL")) if os.environ.get("OPENAI_BASE_URL") else openai.OpenAI()
        resp = client.chat.completions.create(model=self.model, temperature=0, messages=self._messages(image, media_type, context))
        return resp.choices[0].message.content or ""


@dataclass
class LiteLLMVision(OpenAIVision):
    name: str = "litellm"

    def classify(self, image: bytes, media_type: str, context: str) -> str:
        import litellm

        resp = litellm.completion(model=self.model, temperature=0, messages=self._messages(image, media_type, context))
        return resp.choices[0].message.content or ""


@dataclass
class OllamaVision:
    model: str
    name: str = "ollama"
    host: str = "http://localhost:11434"

    def classify(self, image: bytes, media_type: str, context: str) -> str:
        resp = httpx.post(f"{self.host.rstrip('/')}/api/chat", timeout=300, json={
            "model": self.model, "stream": False, "format": "json", "options": {"temperature": 0},
            "messages": [{"role": "system", "content": VISION_SYSTEM_PROMPT},
                         {"role": "user", "content": context, "images": [base64.b64encode(image).decode()]}],
        })
        resp.raise_for_status()
        return (resp.json().get("message") or {}).get("content", "")


def make_vision_model(provider: str, model: str | None = None) -> VisionModel:
    provider = provider.lower()
    if provider == "auto":
        provider = "anthropic" if os.environ.get("ANTHROPIC_API_KEY") else "openai" if os.environ.get("OPENAI_API_KEY") else ""
        if not provider:
            raise ValueError("vision enabled but no ANTHROPIC_API_KEY / OPENAI_API_KEY; choose --vision-provider ollama for a local model")
    if provider == "anthropic":
        return AnthropicVision(model or "claude-sonnet-5-5")
    if provider == "openai":
        return OpenAIVision(model or os.environ.get("OPENAI_MODEL", "gpt-4o"))
    if provider == "litellm":
        if not model:
            raise ValueError("--vision-model is required for litellm")
        return LiteLLMVision(model)
    if provider == "ollama":
        return OllamaVision(model or "llama3.2-vision", host=os.environ.get("OLLAMA_HOST", "http://localhost:11434"))
    raise ValueError(f"unknown vision provider {provider!r}")


# --------------------------------------------------------------------------- #
# Verifier
# --------------------------------------------------------------------------- #


class VisionVerifier:
    def __init__(self, model: VisionModel, mapillary: MapillaryClient | None = None,
                 google: GoogleStreetViewClient | None = None, search_radius_m: int = 60):
        if not (mapillary or google):
            raise ValueError("vision verification needs MAPILLARY_TOKEN or GOOGLE_MAPS_API_KEY")
        self.model = model
        self.mapillary = mapillary
        self.google = google
        self.search_radius_m = search_radius_m

    def verify(self, geo: GeocodeResult, entity_label: str) -> VisionVerification:
        out = VisionVerification(model=f"{self.model.name}:{self.model.model}")
        if not (geo.matched and geo.latitude is not None and geo.longitude is not None):
            out.error = "address not geocoded; no imagery requested"
            return out
        out.performed = True
        try:
            fetched = self._mapillary(geo) if self.mapillary else None
            if fetched is None and self.google:
                fetched = self._google(geo)
        except SourceAPIError as exc:
            out.error = f"imagery retrieval failed: {exc}"
            return out
        if fetched is None:
            out.error = "no street-level imagery found near the geocoded point"
            return out
        imagery, image_bytes, media_type = fetched
        out.imagery = imagery
        context = (f"Photograph taken {imagery.distance_m or 0:.0f} m from, and facing, the geocoded address of record "
                   f"({geo.matched_address}). Capture date: {imagery.captured_at or 'unknown'}. Classify the main structure.")
        try:
            raw = self.model.classify(image_bytes, media_type, context)
        except Exception as exc:  # noqa: BLE001 - provider errors are recorded, never fatal
            out.error = f"vision model error: {type(exc).__name__}: {exc}"[:500]
            return out
        out.raw_model_output = raw[:4000]
        try:
            out.assessment = parse_assessment(raw)
        except ValueError as exc:
            out.error = str(exc)
        return out

    def _mapillary(self, geo: GeocodeResult) -> tuple[ImageryRecord, bytes, str] | None:
        assert self.mapillary and geo.latitude is not None and geo.longitude is not None
        images, rec = self.mapillary.images_near(geo.latitude, geo.longitude, self.search_radius_m)
        best = None
        for img in images:
            coords = (img.get("computed_geometry") or {}).get("coordinates")
            if not coords or not img.get("thumb_1024_url"):
                continue
            dist = haversine_m(geo.latitude, geo.longitude, coords[1], coords[0])
            captured = img.get("captured_at")
            key = (dist > 35, -(captured or 0), dist)  # prefer close, then most recent
            if best is None or key < best[0]:
                best = (key, img, coords, dist)
        if not best:
            return None
        _, img, coords, dist = best
        data, img_rec = self.mapillary.download(img["thumb_1024_url"])
        captured = img.get("captured_at")
        record = ImageryRecord(
            provider="mapillary", image_id=str(img.get("id")),
            captured_at=datetime.fromtimestamp(captured / 1000, tz=timezone.utc).date() if captured else None,
            camera_lat=coords[1], camera_lon=coords[0],
            heading_deg=round(bearing_deg(coords[1], coords[0], geo.latitude, geo.longitude), 1), distance_m=round(dist, 1),
            request_url=f"https://www.mapillary.com/app/?pKey={img.get('id')}", image_sha256=img_rec.response_sha256,
            stored_locally=True, license_note="Mapillary imagery, CC BY-SA 4.0. Attribution: Mapillary contributors.",
            source_record_ids=[rec.record_id, img_rec.record_id],
        )
        media = (img_rec.raw_snippet or {}).get("content_type") if isinstance(img_rec.raw_snippet, dict) else None
        return record, data, sniff_media_type(data, media)

    def _google(self, geo: GeocodeResult) -> tuple[ImageryRecord, bytes, str] | None:
        assert self.google and geo.latitude is not None and geo.longitude is not None
        meta, meta_rec = self.google.metadata(geo.latitude, geo.longitude)
        if meta.get("status") != "OK" or not meta.get("pano_id"):
            return None
        loc = meta.get("location") or {}
        cam_lat, cam_lon = loc.get("lat", geo.latitude), loc.get("lng", geo.longitude)
        heading = bearing_deg(cam_lat, cam_lon, geo.latitude, geo.longitude)
        data, img_rec = self.google.image(meta["pano_id"], heading)
        captured = None
        if meta.get("date"):
            try:
                captured = date.fromisoformat(f"{meta['date']}-01" if len(meta["date"]) == 7 else meta["date"])
            except ValueError:
                captured = None
        record = ImageryRecord(
            provider="google_street_view", image_id=meta["pano_id"], captured_at=captured, camera_lat=cam_lat, camera_lon=cam_lon,
            heading_deg=round(heading, 1), distance_m=round(haversine_m(cam_lat, cam_lon, geo.latitude, geo.longitude), 1),
            request_url=img_rec.url, image_sha256=img_rec.response_sha256, stored_locally=False,
            license_note=f"{meta.get('copyright', '© Google')}. Not stored (Google Maps Platform terms); re-open by pano_id.",
            source_record_ids=[meta_rec.record_id, img_rec.record_id],
        )
        return record, data, sniff_media_type(data)
