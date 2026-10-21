"""Entity and address normalization plus fuzzy matching.

These functions are deterministic and dependency-light. ``rapidfuzz`` is used
when installed; otherwise the standard library ``difflib`` is used so results
remain reproducible on minimal installs (scores differ slightly between the
two backends, and the backend in use is exposed as ``FUZZY_BACKEND``).
"""

from __future__ import annotations

import re
import unicodedata

from .models import Address

try:  # pragma: no cover - exercised implicitly
    from rapidfuzz import fuzz as _rf_fuzz

    FUZZY_BACKEND = "rapidfuzz"
except ImportError:  # pragma: no cover
    _rf_fuzz = None
    FUZZY_BACKEND = "difflib"

import difflib

# --------------------------------------------------------------------------- #
# Business names
# --------------------------------------------------------------------------- #

# Legal-form designators stripped from the *end* (and standalone anywhere) of names.
_ENTITY_SUFFIXES = {
    "LLC", "LC", "LLLP", "LLP", "LP", "PLLC", "PLC", "PC", "PA", "INC", "INCORPORATED",
    "CORP", "CORPORATION", "CO", "COMPANY", "LTD", "LIMITED", "LIABILITY", "PARTNERSHIP",
    "GROUP", "HOLDINGS", "ENTERPRISES", "SERVICES", "SVCS", "INTL", "INTERNATIONAL", "NFP",
    "NONPROFIT", "FOUNDATION",
}
# Suffixes that are strong legal-form designators (always removed). The softer
# words above (GROUP, SERVICES ...) are only removed when trailing.
_HARD_SUFFIXES = {"LLC", "LC", "LLLP", "LLP", "LP", "PLLC", "PLC", "PC", "PA", "INC", "INCORPORATED", "CORP", "CORPORATION", "LTD", "NFP"}
_STOPWORDS = {"THE", "A", "AN", "OF", "AND"}
_DBA_RE = re.compile(r"\b(D/?B/?A|DOING BUSINESS AS|AKA|FKA)\b.*$")


def _ascii_upper(text: str) -> str:
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")
    return text.upper()


def normalize_entity_name(name: str | None) -> str:
    """Canonical comparison form of a business name.

    >>> normalize_entity_name("The Acme Widget Co., L.L.C.")
    'ACME WIDGET'
    """
    if not name:
        return ""
    text = _ascii_upper(name)
    text = _DBA_RE.sub("", text)
    text = text.replace("&", " AND ")
    # Collapse dotted abbreviations like L.L.C. / I.N.C. before stripping punctuation.
    text = re.sub(r"\b((?:[A-Z]\.){2,})", lambda m: m.group(1).replace(".", ""), text)
    text = re.sub(r"[^A-Z0-9 ]+", " ", text)
    tokens = [t for t in text.split() if t not in _STOPWORDS]
    tokens = [t for t in tokens if t not in _HARD_SUFFIXES]
    while len(tokens) > 1 and tokens[-1] in _ENTITY_SUFFIXES:
        tokens.pop()
    return " ".join(tokens)


def normalize_person_name(name: str | None) -> str:
    """Canonical comparison form of a person's name: 'LAST FIRST' tokens sorted, no middle initials/suffixes.

    >>> normalize_person_name("Dr. Jane Q. Sample, MD")
    'JANE SAMPLE'
    """
    if not name:
        return ""
    text = _ascii_upper(name)
    if "," in text:
        # "SAMPLE, JANE Q" -> "JANE Q SAMPLE" (only if the part after the comma isn't a suffix)
        head, _, tail = text.partition(",")
        if tail.strip() and tail.strip().split()[0].strip(".") not in _PERSON_SUFFIXES:
            text = f"{tail} {head}"
        else:
            text = head
    text = re.sub(r"[^A-Z ]+", " ", text)
    tokens = [t for t in text.split() if t not in _PERSON_PREFIXES and t not in _PERSON_SUFFIXES and len(t) > 1]
    return " ".join(sorted(tokens))


_PERSON_PREFIXES = {"MR", "MRS", "MS", "MISS", "DR", "PROF", "REV", "HON"}
_PERSON_SUFFIXES = {"JR", "SR", "II", "III", "IV", "MD", "DO", "PHD", "RN", "NP", "DDS", "DMD", "ESQ", "CPA", "MBA", "LCSW", "DC"}


# --------------------------------------------------------------------------- #
# Addresses
# --------------------------------------------------------------------------- #

# USPS Publication 28, Appendix C1 (common subset) and C2 / B.
STREET_SUFFIXES = {
    "ALLEY": "ALY", "ALLY": "ALY", "ANNEX": "ANX", "AVENUE": "AVE", "AV": "AVE", "AVEN": "AVE", "AVN": "AVE",
    "BEACH": "BCH", "BEND": "BND", "BLUFF": "BLF", "BOULEVARD": "BLVD", "BOUL": "BLVD", "BRANCH": "BR",
    "BRIDGE": "BRG", "BROOK": "BRK", "BYPASS": "BYP", "CAMP": "CP", "CANYON": "CYN", "CAUSEWAY": "CSWY",
    "CENTER": "CTR", "CENTRE": "CTR", "CENTR": "CTR", "CIRCLE": "CIR", "CIRC": "CIR", "CLIFF": "CLF",
    "CLUB": "CLB", "COMMON": "CMN", "CORNER": "COR", "COURSE": "CRSE", "COURT": "CT", "COVE": "CV",
    "CREEK": "CRK", "CRESCENT": "CRES", "CROSSING": "XING", "DALE": "DL", "DRIVE": "DR", "DRIV": "DR",
    "ESTATE": "EST", "ESTATES": "ESTS", "EXPRESSWAY": "EXPY", "EXTENSION": "EXT", "FALLS": "FLS",
    "FERRY": "FRY", "FIELD": "FLD", "FIELDS": "FLDS", "FLAT": "FLT", "FOREST": "FRST", "FORK": "FRK",
    "FORT": "FT", "FREEWAY": "FWY", "GARDEN": "GDN", "GARDENS": "GDNS", "GATEWAY": "GTWY", "GLEN": "GLN",
    "GREEN": "GRN", "GROVE": "GRV", "HARBOR": "HBR", "HAVEN": "HVN", "HEIGHTS": "HTS", "HIGHWAY": "HWY",
    "HIGHWY": "HWY", "HILL": "HL", "HILLS": "HLS", "HOLLOW": "HOLW", "ISLAND": "IS", "JUNCTION": "JCT",
    "KNOLL": "KNL", "LAKE": "LK", "LAKES": "LKS", "LANDING": "LNDG", "LANE": "LN", "LOOP": "LOOP",
    "MANOR": "MNR", "MEADOW": "MDW", "MEADOWS": "MDWS", "MILL": "ML", "MOUNT": "MT", "MOUNTAIN": "MTN",
    "OVERPASS": "OPAS", "PARK": "PARK", "PARKWAY": "PKWY", "PKWAY": "PKWY", "PASS": "PASS", "PATH": "PATH",
    "PIKE": "PIKE", "PINES": "PNES", "PLACE": "PL", "PLAIN": "PLN", "PLAINS": "PLNS", "PLAZA": "PLZ",
    "POINT": "PT", "POINTE": "PT", "PORT": "PRT", "PRAIRIE": "PR", "RANCH": "RNCH", "RIDGE": "RDG",
    "RIVER": "RIV", "ROAD": "RD", "ROUTE": "RTE", "ROW": "ROW", "RUN": "RUN", "SHORE": "SHR",
    "SPRING": "SPG", "SPRINGS": "SPGS", "SQUARE": "SQ", "STATION": "STA", "STREET": "ST", "STR": "ST",
    "STRT": "ST", "SUMMIT": "SMT", "TERRACE": "TER", "TRACE": "TRCE", "TRAIL": "TRL", "TRAILS": "TRL",
    "TURNPIKE": "TPKE", "VALLEY": "VLY", "VIEW": "VW", "VILLAGE": "VLG", "VISTA": "VIS", "WALK": "WALK",
    "WAY": "WAY", "WELLS": "WLS",
}
DIRECTIONALS = {
    "NORTH": "N", "SOUTH": "S", "EAST": "E", "WEST": "W",
    "NORTHEAST": "NE", "NORTHWEST": "NW", "SOUTHEAST": "SE", "SOUTHWEST": "SW",
}
UNIT_DESIGNATORS = {
    "APARTMENT": "APT", "APT": "APT", "BUILDING": "BLDG", "BLDG": "BLDG", "DEPARTMENT": "DEPT", "DEPT": "DEPT",
    "FLOOR": "FL", "FL": "FL", "FLR": "FL", "ROOM": "RM", "RM": "RM", "SUITE": "STE", "STE": "STE", "STES": "STE",
    "UNIT": "UNIT", "SPACE": "SPC", "SPC": "SPC", "LOT": "LOT", "TRAILER": "TRLR", "OFFICE": "OFC", "OFC": "OFC",
    "#": "UNIT", "NO": "UNIT", "NUMBER": "UNIT",
}
# Unit designators that are equivalent for co-location purposes ("STE 4" == "UNIT 4" == "# 4").
_UNIT_EQUIVALENT = {"APT", "STE", "UNIT", "RM", "SPC", "OFC"}

_PO_BOX_RE = re.compile(r"\b(?:P\s*O|POST\s+OFFICE|POB)\s*BOX\s*#?\s*(\w+)|^\s*BOX\s+#?\s*(\d+)", re.I)
_PMB_RE = re.compile(r"\b(?:PMB|PRIVATE\s+MAIL\s*BOX|MAILBOX|MBX)\b", re.I)


def _norm_street_tokens(text: str) -> tuple[str, str | None, bool, bool]:
    """Return (street, unit, is_po_box, is_pmb) from free-text address lines."""
    t = _ascii_upper(text)
    t = re.sub(r"\bP\s*\.\s*O\s*\.?", "PO", t)  # P.O. / P. O. -> PO
    is_pmb = bool(_PMB_RE.search(t))
    box = _PO_BOX_RE.search(t)
    if box:
        return (f"PO BOX {box.group(1) or box.group(2)}", None, True, is_pmb)

    t = t.replace("#", " # ")
    t = re.sub(r"PRIVATE\s+MAIL\s*BOX", "PMB", t)
    t = re.sub(r"[.,;]", " ", t)
    t = re.sub(r"[^A-Z0-9#/\- ]+", " ", t)
    tokens = t.split()

    street: list[str] = []
    unit: str | None = None
    i = 0
    while i < len(tokens):
        tok = tokens[i]
        is_pmb_tok = tok in ("PMB", "MBX", "MAILBOX")
        if (is_pmb_tok or tok in UNIT_DESIGNATORS) and street:
            j = i + 1
            if j < len(tokens) and tokens[j] == "#":
                j += 1
            if j < len(tokens):
                if is_pmb_tok:
                    des = "PMB"
                else:
                    des = UNIT_DESIGNATORS[tok]
                    des = "UNIT" if des in _UNIT_EQUIVALENT else des
                unit = f"{des} {tokens[j]}"
                i = j + 1
                continue
        street.append(DIRECTIONALS.get(tok) or _ordinal(tok))
        i += 1
    # USPS style: only the street-type token (last, or before a trailing directional) is abbreviated,
    # so "4500 LAKE AVENUE" -> "4500 LAKE AVE" rather than "4500 LK AVE".
    if len(street) >= 2:
        pos = len(street) - 1
        if street[pos] in DIRECTIONALS.values() and len(street) >= 3:
            pos -= 1
        street[pos] = STREET_SUFFIXES.get(street[pos], street[pos])
    return (" ".join(street), unit, False, is_pmb)


def _ordinal(tok: str) -> str:
    words = {
        "FIRST": "1ST", "SECOND": "2ND", "THIRD": "3RD", "FOURTH": "4TH", "FIFTH": "5TH",
        "SIXTH": "6TH", "SEVENTH": "7TH", "EIGHTH": "8TH", "NINTH": "9TH", "TENTH": "10TH",
    }
    return words.get(tok, tok)


def normalize_zip(zip_code: str | None) -> tuple[str | None, str | None]:
    if not zip_code:
        return None, None
    digits = re.sub(r"[^0-9]", "", str(zip_code))
    if len(digits) >= 9:
        return digits[:5], digits[5:9]
    if len(digits) >= 5:
        return digits[:5], None
    if len(digits) == 4:  # leading zero dropped by spreadsheets
        return digits.zfill(5), None
    return None, None


def normalize_address(addr: Address) -> Address:
    """Return a copy of ``addr`` with normalized street/unit/zip and PO Box / PMB flags set."""
    raw = " ".join(p for p in (addr.line1, addr.line2) if p)
    street, unit, is_po_box, is_pmb = _norm_street_tokens(raw) if raw else (None, None, False, False)
    zip5, zip4 = normalize_zip(addr.zip5)
    return addr.model_copy(
        update={
            "normalized_street": street or None,
            "normalized_unit": unit,
            "is_po_box": is_po_box,
            "is_pmb": is_pmb,
            "zip5": zip5,
            "zip4": addr.zip4 or zip4,
            "state": addr.state.upper() if addr.state else None,
            "city": _ascii_upper(addr.city).strip() if addr.city else None,
        }
    )


# --------------------------------------------------------------------------- #
# Fuzzy matching
# --------------------------------------------------------------------------- #


def similarity(a: str, b: str) -> float:
    """Token-order-insensitive similarity in [0, 100]."""
    if not a or not b:
        return 0.0
    if _rf_fuzz is not None:
        return float(_rf_fuzz.token_sort_ratio(a, b))
    sa, sb = " ".join(sorted(a.split())), " ".join(sorted(b.split()))
    return round(difflib.SequenceMatcher(None, sa, sb).ratio() * 100, 2)


def set_similarity(a: str, b: str) -> float:
    """Token-*set* similarity in [0, 100]: 100 when one name's tokens are a subset of the other's."""
    if not a or not b:
        return 0.0
    if _rf_fuzz is not None:
        return float(_rf_fuzz.token_set_ratio(a, b))
    ta, tb = set(a.split()), set(b.split())
    inter = " ".join(sorted(ta & tb))
    rest_a, rest_b = " ".join(sorted(ta - tb)), " ".join(sorted(tb - ta))
    pairs = [(inter, f"{inter} {rest_a}".strip()), (inter, f"{inter} {rest_b}".strip()),
             (f"{inter} {rest_a}".strip(), f"{inter} {rest_b}".strip())]
    return round(max(difflib.SequenceMatcher(None, x, y).ratio() for x, y in pairs if x or y) * 100, 2)


def street_number(normalized_street: str | None) -> str | None:
    """Leading house number of a normalized street ('2237 COMMONWEALTH AVE' -> '2237')."""
    m = re.match(r"^(\d+)", normalized_street or "")
    return m.group(1) if m else None


def canonical_org(name: str | None) -> str:
    """normalize_entity_name plus SAINT -> ST, so 'Saint Anthony Park Home' and 'St. Anthony Park Home' compare equal."""
    return " ".join("ST" if t == "SAINT" else t for t in normalize_entity_name(name).split())


def names_related(a: str, b: str, threshold: float = 88.0) -> bool:
    """True if two *normalized* entity names plausibly refer to the same or affiliated organisation."""
    if not a or not b:
        return False
    if a == b:
        return True
    if similarity(a, b) >= threshold:
        return True
    # One name contained in the other with a meaningful length (e.g. 'ACME' vs 'ACME HOME CARE').
    short, long_ = sorted((a, b), key=len)
    return len(short) >= 6 and f" {short} " in f" {long_} "
