"""Program verticals: presets mapping a plain-language sector to award filters.

NAICS/PSC codes filter contracts; Assistance Listing (CFDA) numbers filter
grants and other financial assistance. Operators can extend these with
``--naics`` / ``--psc`` / ``--program`` on the command line.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Vertical:
    key: str
    label: str
    naics_codes: list[str] = field(default_factory=list)
    psc_codes: list[str] = field(default_factory=list)
    program_numbers: list[str] = field(default_factory=list)
    healthcare: bool = False
    caveats: list[str] = field(default_factory=list)


VERTICALS: dict[str, Vertical] = {
    "childcare": Vertical(
        key="childcare",
        label="Child care, early education and child nutrition",
        naics_codes=["624410"],
        program_numbers=[
            "93.575",  # Child Care and Development Block Grant
            "93.596",  # Child Care Mandatory and Matching Funds of the CCDF
            "93.600",  # Head Start
            "10.558",  # Child and Adult Care Food Program
            "10.559",  # Summer Food Service Program for Children
        ],
        caveats=[
            "Licensed family child care homes legitimately operate from residences. A residential "
            "classification is expected for that license type and must be weighed against award size and "
            "licensed capacity before drawing any inference.",
            "CCDF and CACFP funds generally flow through state agencies and sponsoring organizations; the "
            "prime recipient in USASpending may be a pass-through rather than the site operator.",
        ],
    ),
    "healthcare": Vertical(
        key="healthcare",
        label="Health care providers and services",
        naics_codes=["621111", "621210", "621330", "621340", "621399", "621410", "621420", "621491",
                     "621498", "621511", "621610", "621910", "621999", "622110", "623110", "623210", "623312"],
        psc_codes=["Q201", "Q301", "Q401", "Q501", "Q999"],
        program_numbers=["93.224", "93.527", "93.498", "93.461", "93.959", "93.788"],
        healthcare=True,
        caveats=[
            "Some health-related recipients (e.g. grant administrators, research institutions, public health "
            "agencies) are not billing providers and are not required to hold an organizational NPI.",
        ],
    ),
    "home_health": Vertical(
        key="home_health",
        label="Home health, personal care and housing stabilization services",
        naics_codes=["621610", "624120", "623990"],
        program_numbers=["93.778", "93.791", "93.044", "93.045"],
        healthcare=True,
        caveats=[
            "Home-and-community-based service agencies often maintain small administrative offices; "
            "care is delivered in clients' homes, so site visits should target the business office of record.",
        ],
    ),
    "behavioral_health": Vertical(
        key="behavioral_health",
        label="Behavioral health and substance use treatment",
        naics_codes=["621420", "623220"],
        program_numbers=["93.959", "93.243", "93.788", "93.958"],
        healthcare=True,
    ),
    "food_nutrition": Vertical(
        key="food_nutrition",
        label="Food and nutrition program sponsors",
        naics_codes=["624210", "722310", "722320"],
        program_numbers=["10.558", "10.559", "10.555", "10.553"],
    ),
    "housing": Vertical(
        key="housing",
        label="Housing assistance and rental programs",
        naics_codes=["531110", "624229"],
        program_numbers=["14.871", "14.231", "21.023", "14.267"],
    ),
}

# NAICS prefixes treated as healthcare when deciding whether NPPES checks apply.
HEALTHCARE_NAICS_PREFIXES = ("621", "622", "623")
HEALTHCARE_BUSINESS_TYPE_KEYWORDS = ("hospital", "medical", "health", "clinic")
