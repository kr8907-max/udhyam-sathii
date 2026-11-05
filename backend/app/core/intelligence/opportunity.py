
from __future__ import annotations
import logging
from typing import Optional

from .models import (
    SupplyMetrics,
    DemandFeatures,
    OpportunityIndicators,
    RecommendationClass,
)

logger = logging.getLogger("udyam_saathi.intelligence.opportunity")


def calculate_opportunity_indicators(
    supply: SupplyMetrics,
    demand: DemandFeatures,
    category: str = "",
) -> OpportunityIndicators:
    direct_cnt = supply.direct_competitors_count
    core_cnt = supply.direct_in_core_5km

    if direct_cnt == 0:
        supply_gap = 90.0
    elif direct_cnt <= 2:
        supply_gap = 80.0
    elif direct_cnt <= 5:
        supply_gap = 68.0
    elif direct_cnt <= 10:
        supply_gap = 52.0
    elif direct_cnt <= 20:
        supply_gap = 38.0
    else:
        supply_gap = 20.0

    if supply.direct_in_target_locality > 0:
        supply_gap = max(supply_gap - (supply.direct_in_target_locality * 10.0), 15.0)

    dist = supply.nearest_direct_competitor_km
    if dist is None:
        accessibility_gap = 80.0
    elif dist >= 15.0:
        accessibility_gap = 95.0
    elif dist >= 10.0:
        accessibility_gap = 85.0
    elif dist >= 7.0:
        accessibility_gap = 72.0
    elif dist >= 4.0:
        accessibility_gap = 55.0
    elif dist >= 2.0:
        accessibility_gap = 38.0
    else:
        accessibility_gap = 20.0

    pop = demand.population
    if pop >= 10000:
        demand_support = 85.0
    elif pop >= 6000:
        demand_support = 75.0
    elif pop >= 3500:
        demand_support = 65.0
    elif pop >= 1500:
        demand_support = 50.0
    else:
        demand_support = 35.0

    if demand.is_odop_aligned:
        demand_support = min(demand_support + 10.0, 95.0)

    cagr = demand.annual_population_growth_pct
    if cagr >= 1.8:
        growth_support = 85.0
    elif cagr >= 1.2:
        growth_support = 70.0
    elif cagr >= 0.8:
        growth_support = 55.0
    else:
        growth_support = 40.0

    penalty = 0.0
    if demand.power_supply_hours < 12.0:
        penalty += 12.0
    elif demand.power_supply_hours < 16.0:
        penalty += 6.0

    if not demand.all_weather_road:
        penalty += 10.0

    if not demand.commercial_bank_access:
        penalty += 8.0

    cat_upper = category.upper()
    if cat_upper in ("DAIRY", "FOOD_PROCESSING", "POULTRY") and not demand.storage_access:
        penalty += 8.0

    penalty = min(penalty, 40.0)

    return OpportunityIndicators(
        supply_gap_score=round(supply_gap, 1),
        accessibility_gap_score=round(accessibility_gap, 1),
        demand_support_score=round(demand_support, 1),
        growth_support_score=round(growth_support, 1),
        infrastructure_penalty=round(penalty, 1),
    )


def compute_opportunity_score_and_verdict(
    indicators: OpportunityIndicators,
    supply: SupplyMetrics,
    demand: DemandFeatures,
) -> tuple[float, str, str]:
    if not demand.has_sufficient_demand_evidence:
        return 0.0, RecommendationClass.INSUFFICIENT_DATA.value, "INSUFFICIENT_DATA"

    raw_score = (
        (0.35 * indicators.supply_gap_score) +
        (0.25 * indicators.accessibility_gap_score) +
        (0.25 * indicators.demand_support_score) +
        (0.15 * indicators.growth_support_score)
    ) - indicators.infrastructure_penalty

    score = min(max(round(raw_score, 1), 5.0), 95.0)

    resolved_pct = (
        (supply.total_nearby_enterprises - supply.direct_unmapped)
        / max(1, supply.total_nearby_enterprises)
    ) * 100.0 if supply.total_nearby_enterprises > 0 else 100.0

    if resolved_pct >= 60.0 and demand.infrastructure_score > 0:
        confidence = "HIGH"
    elif resolved_pct >= 25.0:
        confidence = "MEDIUM"
    else:
        confidence = "LOW"

    if supply.direct_competitors_count >= 20 and supply.direct_in_core_5km >= 4:
        return score, RecommendationClass.SATURATED_MARKET.value, confidence

    if demand.infrastructure_score < 4.0 or indicators.infrastructure_penalty >= 25.0:
        return score, RecommendationClass.CONSTRAINED_MARKET.value, confidence

    if score >= 68.0:
        verdict = RecommendationClass.HIGH_OPPORTUNITY.value
    elif score >= 48.0:
        verdict = RecommendationClass.MODERATE_OPPORTUNITY.value
    elif supply.direct_competitors_count >= 10:
        verdict = RecommendationClass.SATURATED_MARKET.value
    else:
        verdict = RecommendationClass.CONSTRAINED_MARKET.value

    return score, verdict, confidence
