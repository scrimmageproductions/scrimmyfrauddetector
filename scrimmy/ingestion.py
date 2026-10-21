"""Stage 1 - Ingestion (Target Collector).

Queries USASpending.gov for awards matching the operator's filters, resolves
award rows into ``AwardeeEntity`` objects, and hydrates each entity with its
recipient profile (address, parent, business types) and award details
(signature date, officers, NAICS/PSC/Assistance Listing).
"""

from __future__ import annotations

import logging
from datetime import date, datetime
from typing import Any

from .clients import AWARD_GROUPS, AwardSearchFilters, SourceAPIError, USASpendingClient
from .models import Address, AwardeeEntity, AwardRecord, Officer
from .normalize import normalize_address, normalize_entity_name, normalize_person_name

log = logging.getLogger(__name__)


def parse_date(value: Any) -> date | None:
    if not value:
        return None
    if isinstance(value, date):
        return value
    text = str(value).strip()[:10]
    for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%Y%m%d"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


def _code(value: Any) -> str | None:
    """USASpending returns NAICS/PSC either as a string or as {'code':..., 'description':...}."""
    if isinstance(value, dict):
        return value.get("code")
    return str(value) if value else None


def _address_from_usaspending(loc: dict[str, Any] | None) -> Address:
    loc = loc or {}
    zip_raw = loc.get("zip") or loc.get("zip5") or loc.get("zip_code")
    addr = Address(
        line1=loc.get("address_line1"),
        line2=" ".join(p for p in (loc.get("address_line2"), loc.get("address_line3")) if p) or None,
        city=loc.get("city_name"),
        state=loc.get("state_code"),
        zip5=str(zip_raw) if zip_raw else None,
        zip4=loc.get("zip4"),
        county=loc.get("county_name"),
        country=loc.get("country_code") or loc.get("location_country_code") or "USA",
    )
    return normalize_address(addr)


def entity_key_for_row(row: dict[str, Any]) -> str:
    uei = (row.get("Recipient UEI") or "").strip()
    if uei:
        return f"UEI:{uei.upper()}"
    rid = (row.get("recipient_id") or "").strip()
    if rid:
        return f"RID:{rid.rsplit('-', 1)[0]}"
    return f"NAME:{normalize_entity_name(row.get('Recipient Name'))}"


class TargetCollector:
    def __init__(self, client: USASpendingClient, detail_awards_per_entity: int = 3):
        self.client = client
        self.detail_awards_per_entity = detail_awards_per_entity
        self.failed_groups: dict[str, str] = {}

    # -- search ------------------------------------------------------------ #

    def collect(self, filters: AwardSearchFilters, groups: list[str], max_awards: int, hydrate: bool = True) -> list[AwardeeEntity]:
        entities: dict[str, AwardeeEntity] = {}
        per_group = max(1, max_awards // max(1, len(groups)))
        for group in groups:
            try:
                for row, record in self.client.iter_awards(filters, group, per_group):
                    self._absorb_row(entities, row, group, record.record_id)
            except SourceAPIError as exc:
                log.error("Award search failed for group %s: %s", group, exc)
                self.failed_groups[group] = str(exc)
        log.info("Resolved %d distinct recipients from award search", len(entities))
        if hydrate:
            for entity in entities.values():
                self.hydrate(entity)
        return list(entities.values())

    def _absorb_row(self, entities: dict[str, AwardeeEntity], row: dict[str, Any], group: str, record_id: str) -> None:
        cfg = AWARD_GROUPS[group]
        key = entity_key_for_row(row)
        name = row.get("Recipient Name") or "UNKNOWN RECIPIENT"
        entity = entities.get(key)
        if entity is None:
            uei = (row.get("Recipient UEI") or "").strip().upper() or None
            entity = AwardeeEntity(
                entity_key=key,
                name=name,
                normalized_name=normalize_entity_name(name),
                uei=uei,
                usaspending_recipient_id=row.get("recipient_id"),
            )
            entities[key] = entity
        elif name and name != entity.name and name not in entity.alternate_names:
            entity.alternate_names = [*entity.alternate_names, name]
        if record_id not in entity.source_record_ids:
            entity.source_record_ids = [*entity.source_record_ids, record_id]
        award = AwardRecord(
            award_id=row.get("Award ID"),
            generated_internal_id=row.get("generated_internal_id"),
            award_group=group,
            amount=float(row.get(cfg["amount_field"]) or 0.0),
            start_date=parse_date(row.get(cfg["date_field"])),
            end_date=parse_date(row.get("End Date")),
            awarding_agency=row.get("Awarding Agency"),
            awarding_sub_agency=row.get("Awarding Sub Agency"),
            award_type=row.get(cfg["type_field"]),
            naics_code=_code(row.get("NAICS")),
            psc_code=_code(row.get("PSC")),
            description=row.get("Description"),
            source_record_ids=[record_id],
        )
        if not any(a.generated_internal_id and a.generated_internal_id == award.generated_internal_id for a in entity.awards):
            entity.awards = [*entity.awards, award]

    # -- hydration --------------------------------------------------------- #

    def hydrate(self, entity: AwardeeEntity) -> None:
        if entity.usaspending_recipient_id:
            try:
                profile, record = self.client.recipient_profile(entity.usaspending_recipient_id)
                self._apply_profile(entity, profile, record.record_id)
            except SourceAPIError as exc:
                log.warning("Recipient profile unavailable for %s: %s", entity.name, exc)

        for award in sorted(entity.awards, key=lambda a: a.amount, reverse=True)[: self.detail_awards_per_entity]:
            if not award.generated_internal_id:
                continue
            try:
                detail, record = self.client.award_detail(award.generated_internal_id)
                self._apply_award_detail(entity, award, detail, record.record_id)
            except SourceAPIError as exc:
                log.warning("Award detail unavailable for %s: %s", award.generated_internal_id, exc)

    @staticmethod
    def _apply_profile(entity: AwardeeEntity, profile: dict[str, Any], record_id: str) -> None:
        entity.source_record_ids = [*entity.source_record_ids, record_id]
        entity.uei = entity.uei or (profile.get("uei") or None)
        entity.duns = profile.get("duns") or entity.duns
        entity.parent_uei = profile.get("parent_uei") or entity.parent_uei
        entity.parent_name = profile.get("parent_name") or entity.parent_name
        entity.business_types = sorted(set(entity.business_types) | set(profile.get("business_types") or []))
        for alt in profile.get("alternate_names") or []:
            if alt and alt != entity.name and alt not in entity.alternate_names:
                entity.alternate_names = [*entity.alternate_names, alt]
        loc = profile.get("location")
        if loc and (loc.get("address_line1") or loc.get("zip")):
            entity.address = _address_from_usaspending(loc)

    @staticmethod
    def _apply_award_detail(entity: AwardeeEntity, award: AwardRecord, detail: dict[str, Any], record_id: str) -> None:
        award.source_record_ids = [*award.source_record_ids, record_id]
        award.date_signed = parse_date(detail.get("date_signed")) or award.date_signed
        pop = detail.get("period_of_performance") or {}
        award.start_date = award.start_date or parse_date(pop.get("start_date"))
        contract = detail.get("latest_transaction_contract_data") or {}
        award.naics_code = award.naics_code or contract.get("naics")
        award.psc_code = award.psc_code or contract.get("product_or_service_code")
        cfda = detail.get("cfda_info") or []
        award.program_numbers = sorted({c.get("cfda_number") for c in cfda if c.get("cfda_number")} | set(award.program_numbers))

        recipient = detail.get("recipient") or {}
        if not entity.address.line1 and recipient.get("location"):
            entity.address = _address_from_usaspending(recipient["location"])
        entity.parent_uei = entity.parent_uei or recipient.get("parent_recipient_uei")
        entity.parent_name = entity.parent_name or recipient.get("parent_recipient_name")
        entity.business_types = sorted(set(entity.business_types) | set(recipient.get("business_categories") or []))
        cage = recipient.get("cage_code") or contract.get("cage_code")
        if cage and not entity.cage:
            entity.cage = cage

        officers = (detail.get("executive_details") or {}).get("officers") or []
        for off in officers:
            name = (off or {}).get("name")
            if not name:
                continue
            norm = normalize_person_name(name)
            existing = next((o for o in entity.officers if o.normalized_name == norm), None)
            if existing:
                if record_id not in existing.source_record_ids:
                    existing.source_record_ids = [*existing.source_record_ids, record_id]
                continue
            entity.officers = [
                *entity.officers,
                Officer(name=name, normalized_name=norm, title="Highly compensated officer",
                        role_source="usaspending.executive_details", source_record_ids=[record_id]),
            ]
