
from __future__ import annotations
import math
from typing import Optional
from ..models import MarketZone


EARTH_RADIUS_KM = 6371.0


def haversine_distance(
    lat1: float,
    lon1: float,
    lat2: float,
    lon2: float,
) -> float:
    lat1_r = math.radians(lat1)
    lat2_r = math.radians(lat2)
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)

    a = (
        math.sin(dlat / 2) ** 2
        + math.cos(lat1_r) * math.cos(lat2_r) * math.sin(dlon / 2) ** 2
    )
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))

    return EARTH_RADIUS_KM * c


def calculate_distance(
    target_lat: Optional[float],
    target_lon: Optional[float],
    business_lat: Optional[float],
    business_lon: Optional[float],
) -> Optional[float]:
    if target_lat is None or target_lon is None:
        return None
    if business_lat is None or business_lon is None:
        return None

    if not (-90 <= target_lat <= 90 and -180 <= target_lon <= 180):
        return None
    if not (-90 <= business_lat <= 90 and -180 <= business_lon <= 180):
        return None

    return haversine_distance(target_lat, target_lon, business_lat, business_lon)


def classify_market_zone(
    distance_km: Optional[float],
    core_radius_km: float = 5.0,
    nearby_radius_km: float = 10.0,
) -> MarketZone:
    if distance_km is None:
        return MarketZone.UNMAPPED

    if distance_km <= core_radius_km:
        return MarketZone.CORE_5KM
    elif distance_km <= nearby_radius_km:
        return MarketZone.NEARBY_10KM
    else:
        return MarketZone.OUTSIDE_10KM


def classify_with_custom_zones(
    distance_km: Optional[float],
    zone_boundaries: list[tuple[str, float]],
) -> str:
    if distance_km is None:
        return "UNMAPPED"

    for zone_name, max_dist in sorted(zone_boundaries, key=lambda x: x[1]):
        if distance_km <= max_dist:
            return zone_name

    return "OUTSIDE"
