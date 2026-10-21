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
        label="Child day care and child nutrition services",
        naics_codes=["624410", "624210"],
        program_numbers=[
            "93.575",  # Child Care and Development Block Grant
            "93.596",  # Child Care Mandatory and Matching Funds of the CCDF
            "93.600",  # Head Start
            "10.558",  # Child and Adult Care Food Program
            "10.559",  # Summer Food Service Program for Children
        ],
        caveats=[
            "Licensed family child care homes legitimately operate from residences. A residential classification is "
            "expected for that license type and must be weighed against award size and licensed capacity.",
            "CCDF and CACFP funds generally flow through state agencies and sponsoring organizations; the prime recipient "
            "in USASpending may be a pass-through or sponsor rather than the site operator.",
        ],
    ),
    "home_health": Vertical(
        key="home_health",
        label="Home health care services",
        naics_codes=["621610"],
        program_numbers=["93.778", "93.791", "93.044", "93.045"],
        healthcare=True,
        caveats=[
            "Home health agencies deliver care in clients' homes and often keep small administrative offices, so premises "
            "appearance is weak evidence; site visits should target the business office of record.",
        ],
    ),
    "hospice": Vertical(
        key="hospice",
        label="Hospice, nursing and elder-care facilities",
        naics_codes=["623110", "624120"],
        program_numbers=["93.778", "93.044", "93.045", "93.791"],
        healthcare=True,
        caveats=[
            "Hospice is reimbursed per patient-day and is often delivered in patients' residences or nursing facilities; "
            "a hospice agency's office need not house patients. 623110 also covers skilled nursing facilities.",
        ],
    ),
    "clinics": Vertical(
        key="clinics",
        label="Specialized medical and outpatient clinics",
        naics_codes=["621498", "621999"],
        program_numbers=["93.224", "93.527", "93.498", "93.959", "93.788"],
        healthcare=True,
        caveats=[
            "Some health-related recipients (grant administrators, research institutions, public health agencies) are not "
            "billing providers and are not required to hold an organizational NPI.",
        ],
    ),
    "healthcare": Vertical(
        key="healthcare",
        label="Health care providers and services (broad)",
        naics_codes=["621111", "621210", "621330", "621340", "621399", "621410", "621420", "621491",
                     "621498", "621511", "621610", "621910", "621999", "622110", "623110", "623210", "623312"],
        psc_codes=["Q201", "Q301", "Q401", "Q501", "Q999"],
        program_numbers=["93.224", "93.527", "93.498", "93.461", "93.959", "93.788"],
        healthcare=True,
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

# The default target set when no vertical is chosen ("DOJ priority verticals" in the project brief).
PRIORITY_VERTICALS = ["childcare", "home_health", "hospice"]

# NAICS prefixes treated as healthcare when deciding whether NPPES checks apply.
HEALTHCARE_NAICS_PREFIXES = ("621", "622", "623")
HEALTHCARE_BUSINESS_TYPE_KEYWORDS = ("hospital", "medical", "health", "clinic")
