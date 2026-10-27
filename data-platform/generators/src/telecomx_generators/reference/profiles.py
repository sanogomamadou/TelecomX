"""The inputs of the reference-data generator: regional profiles, offers, alarm catalogue.

Kept apart from the construction logic so that the *assumptions* can be read, and
challenged, without reading the algorithm.

Honesty about realism
---------------------
Everything below is **synthetic**. The regional shares and centroids are rough
orders of magnitude chosen to produce a plausible network shape (a dense capital, a
sparse and satellite-linked north), not statistics. Site coordinates are random
points around a centroid: **no site corresponds to a real tower**. The vendors are
invented. The point is a coherent, plausible dataset to build and test a platform on,
not a model of any real operator.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Final

from telecom_common.domain.enums import AreaType, Technology

__all__ = [
    "ALARM_TYPES",
    "BUNDLES",
    "REFERENCE_DATE",
    "REGION_PROFILES",
    "TECHNOLOGIES",
    "TENANTS",
    "RegionProfile",
]

#: "Today" for the dataset. A fixed date, never the clock: reference data must be
#: reproducible, and an age computed from the wall clock would change every day.
REFERENCE_DATE: Final = date(2026, 9, 1)


@dataclass(frozen=True, slots=True)
class RegionProfile:
    """Shape of one region of the synthetic country."""

    code: str
    name: str
    area_type: AreaType
    population_share: float
    subscriber_share: float  # share of the operator's subscribers
    area_share: float  # share of the national territory
    urbanization: float  # probability that a site there is an urban site
    centroid: tuple[float, float]  # (latitude, longitude), approximate
    spread_deg: float  # standard deviation of site positions around the centroid
    fiber_share: float  # base probability of a fibre backhaul
    satellite_share: float  # probability of a satellite backhaul on a non-urban site
    backup_power_share: float  # probability that a site has batteries


#: Order matters: it fixes site numbering, and the capital must come first so that
#: SITE-102 of the demonstration scenario lies in Bamako.
REGION_PROFILES: Final[tuple[RegionProfile, ...]] = (
    RegionProfile(
        "BKO",
        "Bamako",
        AreaType.URBAN,
        0.13,
        0.28,
        0.01,
        0.90,
        (12.64, -8.00),
        0.05,
        0.65,
        0.00,
        0.60,
    ),
    RegionProfile(
        "SKS",
        "Sikasso",
        AreaType.PERIURBAN,
        0.16,
        0.14,
        0.08,
        0.30,
        (11.32, -5.67),
        0.45,
        0.20,
        0.02,
        0.50,
    ),
    RegionProfile(
        "KLK",
        "Koulikoro",
        AreaType.PERIURBAN,
        0.16,
        0.13,
        0.08,
        0.30,
        (12.86, -7.56),
        0.50,
        0.25,
        0.02,
        0.50,
    ),
    RegionProfile(
        "SGU",
        "Ségou",
        AreaType.PERIURBAN,
        0.15,
        0.12,
        0.09,
        0.28,
        (13.43, -6.27),
        0.50,
        0.20,
        0.03,
        0.50,
    ),
    RegionProfile(
        "KYS",
        "Kayes",
        AreaType.RURAL,
        0.10,
        0.09,
        0.10,
        0.22,
        (14.45, -11.44),
        0.60,
        0.12,
        0.05,
        0.55,
    ),
    RegionProfile(
        "MPT",
        "Mopti",
        AreaType.RURAL,
        0.14,
        0.10,
        0.09,
        0.20,
        (14.49, -4.20),
        0.60,
        0.10,
        0.10,
        0.60,
    ),
    RegionProfile(
        "TBT",
        "Tombouctou",
        AreaType.RURAL,
        0.06,
        0.05,
        0.35,
        0.10,
        (16.77, -3.01),
        1.20,
        0.03,
        0.30,
        0.80,
    ),
    RegionProfile(
        "GAO", "Gao", AreaType.RURAL, 0.07, 0.06, 0.15, 0.15, (16.27, -0.04), 0.90, 0.05, 0.25, 0.80
    ),
    RegionProfile(
        "KDL",
        "Kidal",
        AreaType.RURAL,
        0.03,
        0.03,
        0.05,
        0.05,
        (18.44, 1.41),
        0.80,
        0.00,
        0.50,
        0.85,
    ),
)

TENANTS: Final = (
    ("telecomx-ml", "TelecomX Mali", "ML", "XOF"),
    ("telecomx-sn", "TelecomX Sénégal", "SN", "XOF"),
    ("telecomx-dev", "TelecomX Sandbox", "ML", "XOF"),
)

#: (technology, label, theoretical downlink Mbps, reference latency ms)
TECHNOLOGIES: Final = (
    (Technology.G2, "2G (GSM/EDGE)", 0.236, 300.0),
    (Technology.G3, "3G (UMTS/HSPA)", 42.0, 100.0),
    (Technology.G4, "4G (LTE)", 300.0, 40.0),
    (Technology.G5, "5G (NR)", 1000.0, 15.0),
)

# --- Commercial offers ------------------------------------------------------
# (code, label, type, allowance_mb, allowance_minutes, price_xof, validity_days)
# DATA-10GB at 5 000 XOF is the figure of the PRD example.

_Offer = tuple[str, str, str, float | None, int | None, int, int]

_DATA: list[_Offer] = [
    ("DATA-200MB-1D", "200 Mo, 1 jour", "DATA", 200.0, None, 200, 1),
    ("DATA-1GB-1D", "1 Go, 1 jour", "DATA", 1024.0, None, 500, 1),
    ("DATA-NIGHT-5GB", "5 Go de nuit, 1 jour", "DATA", 5120.0, None, 400, 1),
    ("DATA-1GB-7D", "1 Go, 7 jours", "DATA", 1024.0, None, 600, 7),
    ("DATA-3GB-7D", "3 Go, 7 jours", "DATA", 3072.0, None, 1500, 7),
    ("DATA-10GB-7D", "10 Go, 7 jours", "DATA", 10240.0, None, 3500, 7),
    ("DATA-500MB", "500 Mo, 30 jours", "DATA", 500.0, None, 500, 30),
    ("DATA-1GB", "1 Go, 30 jours", "DATA", 1024.0, None, 1000, 30),
    ("DATA-2GB", "2 Go, 30 jours", "DATA", 2048.0, None, 1800, 30),
    ("DATA-5GB", "5 Go, 30 jours", "DATA", 5120.0, None, 3000, 30),
    ("DATA-10GB", "10 Go, 30 jours", "DATA", 10240.0, None, 5000, 30),
    ("DATA-20GB", "20 Go, 30 jours", "DATA", 20480.0, None, 8000, 30),
    ("DATA-50GB", "50 Go, 30 jours", "DATA", 51200.0, None, 15000, 30),
]
_VOICE: list[_Offer] = [
    ("VOICE-30MIN-7D", "30 minutes, 7 jours", "VOICE", None, 30, 500, 7),
    ("VOICE-100MIN", "100 minutes, 30 jours", "VOICE", None, 100, 1000, 30),
    ("VOICE-300MIN", "300 minutes, 30 jours", "VOICE", None, 300, 2500, 30),
    ("VOICE-1000MIN", "1000 minutes, 30 jours", "VOICE", None, 1000, 6000, 30),
]
_SMS: list[_Offer] = [
    ("SMS-100", "100 SMS, 30 jours", "SMS", None, None, 300, 30),
    ("SMS-500", "500 SMS, 30 jours", "SMS", None, None, 1000, 30),
]
_MIXED: list[_Offer] = [
    ("MIX-S", "Mixte S : 1 Go + 30 min", "MIXED", 1024.0, 30, 1500, 30),
    ("MIX-M", "Mixte M : 3 Go + 100 min", "MIXED", 3072.0, 100, 3500, 30),
    ("MIX-L", "Mixte L : 8 Go + 300 min", "MIXED", 8192.0, 300, 7000, 30),
    ("MIX-XL", "Mixte XL : 20 Go + 1000 min", "MIXED", 20480.0, 1000, 15000, 30),
    ("MIX-BUSINESS", "Mixte Entreprise : 50 Go + 3000 min", "MIXED", 51200.0, 3000, 40000, 30),
]
_SOCIAL: list[_Offer] = [
    ("SOCIAL-WA-1D", "Messagerie, 1 jour", "SOCIAL", 100.0, None, 100, 1),
    ("SOCIAL-WA-7D", "Messagerie, 7 jours", "SOCIAL", 500.0, None, 400, 7),
    ("SOCIAL-FB-7D", "Réseaux sociaux, 7 jours", "SOCIAL", 1500.0, None, 700, 7),
    ("SOCIAL-FULL-30D", "Réseaux sociaux illimités, 30 jours", "SOCIAL", 8192.0, None, 2500, 30),
]

BUNDLES: Final[tuple[_Offer, ...]] = tuple(_DATA + _VOICE + _SMS + _MIXED + _SOCIAL)

# --- Alarm catalogue ---------------------------------------------------------
# (code, label, domain, default severity, procedure)
# The procedure references are forward references: the documents are written for the
# RAG corpus in Phase 4. SOP-NET-014 (packet loss) and SOP-NET-007 (drop rate) are the
# ones the demonstration scenario cites.

_Alarm = tuple[str, str, str, str, str]

ALARM_TYPES: Final[tuple[_Alarm, ...]] = (
    # Radio
    ("CELL_DOWN", "Cellule hors service", "RADIO", "CRITICAL", "SOP-NET-001"),
    ("SECTOR_OUTAGE", "Panne de secteur", "RADIO", "MAJOR", "SOP-NET-002"),
    ("TRX_FAILURE", "Défaillance d'émetteur-récepteur", "RADIO", "MAJOR", "SOP-NET-003"),
    ("RRU_COMM_LOST", "Communication perdue avec l'unité radio", "RADIO", "MAJOR", "SOP-NET-004"),
    ("VSWR_HIGH", "Taux d'ondes stationnaires élevé", "RADIO", "MINOR", "SOP-NET-005"),
    ("PIM_DETECTED", "Intermodulation passive détectée", "RADIO", "MINOR", "SOP-NET-006"),
    ("HIGH_DROP_RATE", "Taux de coupure d'appels élevé", "RADIO", "MAJOR", "SOP-NET-007"),
    (
        "CALL_SETUP_FAILURE_HIGH",
        "Échecs d'établissement d'appel élevés",
        "RADIO",
        "MAJOR",
        "SOP-NET-008",
    ),
    (
        "HANDOVER_FAILURE_HIGH",
        "Échecs de transfert intercellulaire élevés",
        "RADIO",
        "MINOR",
        "SOP-NET-009",
    ),
    ("RF_INTERFERENCE", "Interférence en voie montante", "RADIO", "WARNING", "SOP-NET-010"),
    (
        "PRB_UTILIZATION_HIGH",
        "Occupation des ressources radio élevée",
        "RADIO",
        "WARNING",
        "SOP-NET-011",
    ),
    ("ANTENNA_TILT_FAULT", "Défaut d'inclinaison d'antenne", "RADIO", "MINOR", "SOP-NET-012"),
    # Transport
    ("LINK_DOWN", "Lien de transport coupé", "TRANSPORT", "CRITICAL", "SOP-NET-013"),
    ("PACKET_LOSS_HIGH", "Perte de paquets élevée", "TRANSPORT", "MAJOR", "SOP-NET-014"),
    ("BACKHAUL_CONGESTION", "Congestion du backhaul", "TRANSPORT", "MAJOR", "SOP-NET-014"),
    ("CRC_ERRORS_HIGH", "Erreurs CRC en hausse", "TRANSPORT", "MINOR", "SOP-NET-015"),
    ("LATENCY_HIGH", "Latence de transport élevée", "TRANSPORT", "MINOR", "SOP-NET-016"),
    ("MW_FADING", "Évanouissement du lien hertzien", "TRANSPORT", "MINOR", "SOP-NET-017"),
    ("FIBER_CUT", "Coupure de fibre suspectée", "TRANSPORT", "CRITICAL", "SOP-NET-018"),
    ("ROUTER_CPU_HIGH", "Charge processeur du routeur élevée", "TRANSPORT", "MAJOR", "SOP-NET-019"),
    ("SYNC_LOSS", "Perte de synchronisation d'horloge", "TRANSPORT", "MAJOR", "SOP-NET-020"),
    ("BGP_SESSION_DOWN", "Session de routage interrompue", "TRANSPORT", "MAJOR", "SOP-NET-021"),
    ("SATELLITE_LINK_DEGRADED", "Lien satellite dégradé", "TRANSPORT", "MINOR", "SOP-NET-022"),
    # Core
    ("SIGNALING_OVERLOAD", "Surcharge de la signalisation", "CORE", "MAJOR", "SOP-COR-001"),
    (
        "CHARGING_FAILURE",
        "Défaillance de la facturation temps réel",
        "CORE",
        "CRITICAL",
        "SOP-COR-002",
    ),
    ("SMSC_BACKLOG", "File d'attente SMS saturée", "CORE", "MAJOR", "SOP-COR-003"),
    (
        "USSD_GATEWAY_TIMEOUT",
        "Délais dépassés sur la passerelle USSD",
        "CORE",
        "MAJOR",
        "SOP-COR-004",
    ),
    (
        "MOMO_PLATFORM_DEGRADED",
        "Plateforme Mobile Money dégradée",
        "CORE",
        "CRITICAL",
        "SOP-COR-005",
    ),
    (
        "DB_REPLICATION_LAG",
        "Retard de réplication de base de données",
        "CORE",
        "MINOR",
        "SOP-COR-006",
    ),
    ("HLR_UNAVAILABLE", "Registre des abonnés indisponible", "CORE", "CRITICAL", "SOP-COR-007"),
    (
        "DATA_SESSION_FAILURES_HIGH",
        "Échecs de sessions de données élevés",
        "CORE",
        "MAJOR",
        "SOP-COR-008",
    ),
    # Power
    ("MAINS_FAIL", "Coupure du secteur", "POWER", "MAJOR", "SOP-PWR-001"),
    ("BATTERY_LOW", "Batterie faible", "POWER", "MAJOR", "SOP-PWR-002"),
    ("GENSET_FAULT", "Défaut du groupe électrogène", "POWER", "MAJOR", "SOP-PWR-003"),
    ("GENSET_FUEL_LOW", "Carburant du groupe électrogène bas", "POWER", "MINOR", "SOP-PWR-004"),
    ("RECTIFIER_FAULT", "Défaut de redresseur", "POWER", "MAJOR", "SOP-PWR-005"),
    ("VOLTAGE_UNSTABLE", "Tension d'alimentation instable", "POWER", "WARNING", "SOP-PWR-006"),
    ("BATTERY_END_OF_LIFE", "Batterie en fin de vie", "POWER", "WARNING", "SOP-PWR-007"),
    ("POWER_SUPPLY_FAULT", "Défaut d'alimentation électrique", "POWER", "MAJOR", "SOP-PWR-008"),
    # Environment
    ("HIGH_TEMPERATURE", "Température élevée", "ENVIRONMENT", "MAJOR", "SOP-ENV-001"),
    ("HVAC_FAULT", "Défaut de climatisation", "ENVIRONMENT", "MINOR", "SOP-ENV-002"),
    ("DOOR_OPEN", "Porte de l'abri ouverte", "ENVIRONMENT", "WARNING", "SOP-ENV-003"),
    ("SMOKE_DETECTED", "Fumée détectée", "ENVIRONMENT", "CRITICAL", "SOP-ENV-004"),
    ("FLOOD_DETECTED", "Inondation détectée", "ENVIRONMENT", "CRITICAL", "SOP-ENV-005"),
    ("THEFT_ALARM", "Alarme anti-intrusion", "ENVIRONMENT", "MAJOR", "SOP-ENV-006"),
    ("FAN_FAILURE", "Défaillance de ventilation", "ENVIRONMENT", "MINOR", "SOP-ENV-007"),
    # Platform (raised by the platform itself, not by network equipment)
    ("ANOMALY_DETECTED", "Anomalie détectée par le modèle", "PLATFORM", "WARNING", "SOP-PLT-001"),
    ("FAILURE_PREDICTED", "Panne d'équipement prédite", "PLATFORM", "WARNING", "SOP-PLT-002"),
    (
        "CAPACITY_SATURATION_RISK",
        "Risque de saturation prévu",
        "PLATFORM",
        "WARNING",
        "SOP-PLT-003",
    ),
    ("DATA_FRESHNESS_BREACH", "Fraîcheur des données dépassée", "PLATFORM", "MINOR", "SOP-PLT-004"),
    ("MODEL_DRIFT_DETECTED", "Dérive de modèle détectée", "PLATFORM", "MINOR", "SOP-PLT-005"),
)
