
from __future__ import annotations
from enum import Enum
from dataclasses import dataclass, asdict, field
from typing import Optional, Any
from datetime import datetime, timezone


class RelevanceClass(str, Enum):
    DIRECT_COMPETITOR = "DIRECT_COMPETITOR"
    RELATED_BUSINESS = "RELATED_BUSINESS"
    INDIRECT_COMPETITOR = "INDIRECT_COMPETITOR"
    NON_RELEVANT = "NON_RELEVANT"
    UNKNOWN = "UNKNOWN"


class RecommendationClass(str, Enum):
    HIGH_OPPORTUNITY = "HIGH_OPPORTUNITY"
    MODERATE_OPPORTUNITY = "MODERATE_OPPORTUNITY"
    SATURATED_MARKET = "SATURATED_MARKET"
    CONSTRAINED_MARKET = "CONSTRAINED_MARKET"
    INSUFFICIENT_DATA = "INSUFFICIENT_DATA"


@dataclass
class BusinessIntent:
    intent_id: str
    display_name: str
    primary_category: str
    direct_categories: list[str] = field(default_factory=list)
    related_categories: list[str] = field(default_factory=list)
    search_keywords: list[str] = field(default_factory=list)
    description: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class CompetitorRecord:
    enterprise_name: str
    record_fingerprint: str
    category: str
    category_label: str
    subcategory: str = ""
    relevance_class: str = RelevanceClass.UNKNOWN.value
    relevance_reason: str = ""
    relevance_confidence: str = "UNKNOWN"
    distance_km: Optional[float] = None
    market_zone: str = "UNMAPPED"
    resolved_locality: str = ""
    geographic_confidence: str = "UNKNOWN"
    registration_date: Optional[str] = None
    nic_code: str = ""
    activity_description: str = ""
    data_sources: list[str] = field(default_factory=lambda: ["UDYAM_MSME"])
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    coordinate_confidence: str = "UNKNOWN"
    coordinate_source: str = ""
    activities_parsed: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class SupplyMetrics:
    total_nearby_enterprises: int = 0
    direct_competitors_count: int = 0
    related_businesses_count: int = 0
    indirect_competitors_count: int = 0
    non_relevant_count: int = 0
    unknown_relevance_count: int = 0

    nearest_direct_competitor_km: Optional[float] = None
    nearest_related_business_km: Optional[float] = None
    median_direct_competitor_distance_km: Optional[float] = None

    direct_in_core_5km: int = 0
    direct_in_nearby_10km: int = 0
    direct_outside_10km: int = 0
    direct_unmapped: int = 0

    direct_in_target_locality: int = 0
    share_in_target_locality_pct: float = 0.0

    category_share_pct: float = 0.0
    hhi_concentration_index: float = 0.0
    businesses_per_1000_people: Optional[float] = None

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class DemandFeatures:
    population: int = 0
    households: int = 0
    annual_population_growth_pct: float = 0.0
    infrastructure_score: float = 5.0
    power_supply_hours: float = 18.0
    all_weather_road: bool = True
    commercial_bank_access: bool = True
    internet_access: bool = True
    storage_access: bool = False
    is_odop_aligned: bool = False
    odop_product_name: Optional[str] = None
    data_years: dict[str, str] = field(default_factory=dict)
    sources: list[str] = field(default_factory=list)
    has_sufficient_demand_evidence: bool = True

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class OpportunityIndicators:
    supply_gap_score: float = 50.0
    accessibility_gap_score: float = 50.0
    demand_support_score: float = 50.0
    growth_support_score: float = 50.0
    infrastructure_penalty: float = 0.0

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class EvidenceObject:
    recommendation: str
    composite_score: float
    confidence: str
    confidence_reasons: list[str] = field(default_factory=list)
    top_positive_drivers: list[str] = field(default_factory=list)
    top_risk_factors: list[str] = field(default_factory=list)
    missing_evidence: list[str] = field(default_factory=list)
    data_quality_limitations: list[str] = field(default_factory=list)
    plain_language_summary: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class SensitivityScenario:
    scenario_name: str
    radius_km: float
    competitors_count: int
    nearest_km: Optional[float]
    opportunity_score: float
    recommendation: str

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class MarketOpportunityReport:
    target_location: dict[str, Any]
    business_intent: dict[str, Any]
    recommendation: str
    composite_score: float
    confidence: str

    supply_metrics: dict[str, Any]
    demand_features: dict[str, Any]
    opportunity_indicators: dict[str, Any]

    classified_competitors: list[dict[str, Any]]
    all_nearby_businesses: list[dict[str, Any]]

    evidence: dict[str, Any]
    sensitivity_analysis: list[dict[str, Any]]

    data_lineage: list[dict[str, Any]]
    generated_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    version: str = "2.0-deterministic"

    def to_dict(self) -> dict:
        return asdict(self)
