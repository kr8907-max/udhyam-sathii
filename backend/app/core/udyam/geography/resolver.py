
from __future__ import annotations
import logging
from typing import Optional
from ..models import (
    UdyamCanonicalRecord,
    GeographicResolution,
    GeographicCandidate,
    TargetLocation,
    LocalityConfidence,
    CoordinateConfidence,
    CoordinateSource,
    MarketZone,
)
from ..normalization import normalize_address, normalize_pincode
from .extraction import extract_geographic_candidates
from .gazetteer import Gazetteer
from .distance import calculate_distance, classify_market_zone

logger = logging.getLogger("udyam_saathi.resolver")


class GeographicResolver:

    def __init__(self, gazetteer: Gazetteer):
        self.gazetteer = gazetteer

    def resolve_record(
        self,
        record: UdyamCanonicalRecord,
        target: Optional[TargetLocation] = None,
        core_radius_km: float = 5.0,
        nearby_radius_km: float = 10.0,
    ) -> GeographicResolution:
        resolution = GeographicResolution(
            record_fingerprint=record.record_fingerprint,
        )

        normalized_addr = record.communication_address_normalized
        if not normalized_addr:
            normalized_addr = normalize_address(record.communication_address_raw)

        candidates = extract_geographic_candidates(
            normalized_address=normalized_addr,
            known_state=record.state_normalized,
            known_district=record.district_normalized,
            known_pincode=record.pincode_normalized,
        )
        resolution.locality_candidates = [c.to_dict() for c in candidates]

        if not candidates:
            resolution.locality_confidence = LocalityConfidence.UNKNOWN.value
            resolution.coordinate_confidence = CoordinateConfidence.UNKNOWN.value
            resolution.resolution_error = "ADDRESS_TOO_SPARSE"
            resolution.market_zone = MarketZone.UNMAPPED.value
            return resolution

        best_match = self._find_best_match(
            candidates=candidates,
            state=record.state_normalized,
            district=record.district_normalized,
        )

        if best_match is not None:
            resolution.resolved_locality = best_match.normalized_name
            resolution.locality_confidence = self._compute_locality_confidence(
                best_match, candidates, record
            )

            if best_match.latitude is not None and best_match.longitude is not None:
                resolution.latitude = best_match.latitude
                resolution.longitude = best_match.longitude
                resolution.coordinate_confidence = best_match.coordinate_confidence
                resolution.coordinate_source = best_match.source
                resolution.resolution_method = "gazetteer_match"
            else:
                resolution.coordinate_confidence = CoordinateConfidence.UNKNOWN.value
                resolution.coordinate_source = CoordinateSource.UNKNOWN.value
                resolution.resolution_method = "gazetteer_locality_only"
        else:
            pin_result = self._resolve_via_pincode(
                pincode=record.pincode_normalized,
                candidates=candidates,
                state=record.state_normalized,
                district=record.district_normalized,
            )

            if pin_result is not None:
                resolution.resolved_locality = pin_result.get("locality", "")
                resolution.locality_confidence = LocalityConfidence.LOW.value
                resolution.resolution_method = "pincode_centroid"

                if pin_result.get("latitude") is not None:
                    resolution.latitude = pin_result["latitude"]
                    resolution.longitude = pin_result["longitude"]
                    resolution.coordinate_confidence = CoordinateConfidence.LOW.value
                    resolution.coordinate_source = CoordinateSource.PINCODE_DIRECTORY.value
                else:
                    resolution.coordinate_confidence = CoordinateConfidence.UNKNOWN.value
            else:
                locality_candidates = [
                    c for c in candidates
                    if c.entity_type in ("village", "locality", "hamlet", "town")
                ]
                if locality_candidates:
                    resolution.resolved_locality = locality_candidates[0].text
                    resolution.locality_confidence = LocalityConfidence.LOW.value
                else:
                    resolution.locality_confidence = LocalityConfidence.UNKNOWN.value

                resolution.coordinate_confidence = CoordinateConfidence.UNKNOWN.value
                resolution.resolution_error = "UNKNOWN_LOCALITY"
                resolution.resolution_method = "unresolved"

        if target is not None:
            resolution.distance_km = calculate_distance(
                target_lat=target.latitude,
                target_lon=target.longitude,
                business_lat=resolution.latitude,
                business_lon=resolution.longitude,
            )
            resolution.market_zone = classify_market_zone(
                distance_km=resolution.distance_km,
                core_radius_km=core_radius_km,
                nearby_radius_km=nearby_radius_km,
            ).value
        else:
            resolution.market_zone = MarketZone.UNMAPPED.value

        return resolution

    def resolve_batch(
        self,
        records: list[UdyamCanonicalRecord],
        target: Optional[TargetLocation] = None,
        core_radius_km: float = 5.0,
        nearby_radius_km: float = 10.0,
    ) -> list[GeographicResolution]:
        results = []
        for record in records:
            try:
                res = self.resolve_record(
                    record=record,
                    target=target,
                    core_radius_km=core_radius_km,
                    nearby_radius_km=nearby_radius_km,
                )
                results.append(res)
            except Exception as e:
                logger.error(
                    f"Resolution failed for {record.record_fingerprint[:12]}: {e}",
                    exc_info=True,
                )
                res = GeographicResolution(
                    record_fingerprint=record.record_fingerprint,
                    resolution_error=f"RESOLUTION_EXCEPTION: {type(e).__name__}: {str(e)[:100]}",
                    locality_confidence=LocalityConfidence.UNKNOWN.value,
                    coordinate_confidence=CoordinateConfidence.UNKNOWN.value,
                    market_zone=MarketZone.UNMAPPED.value,
                )
                results.append(res)

        return results

    def _find_best_match(
        self,
        candidates: list[GeographicCandidate],
        state: str,
        district: str,
    ) -> Optional:
        best_match = None
        best_score = -1

        marker_candidates = [c for c in candidates if c.method == "admin_marker"]
        positional_candidates = [c for c in candidates if c.method == "positional_heuristic"]

        for candidate_list, base_score in [
            (marker_candidates, 10),
            (positional_candidates, 5),
        ]:
            for candidate in candidate_list:
                matches = self.gazetteer.lookup_by_name(
                    name=candidate.text,
                    state=state,
                    district=district,
                )

                if matches:
                    for match in matches:
                        score = base_score
                        if match.coordinate_confidence == CoordinateConfidence.HIGH.value:
                            score += 5
                        elif match.coordinate_confidence == CoordinateConfidence.MEDIUM.value:
                            score += 3
                        elif match.coordinate_confidence == CoordinateConfidence.LOW.value:
                            score += 1

                        if match.village and match.village.upper() == candidate.text:
                            score += 3

                        if score > best_score:
                            best_score = score
                            best_match = match
                    continue

                matches = self.gazetteer.lookup_by_name(
                    name=candidate.text,
                    state=state,
                )
                if matches and len(matches) == 1:
                    match = matches[0]
                    score = base_score - 2
                    if score > best_score:
                        best_score = score
                        best_match = match

        return best_match

    def _resolve_via_pincode(
        self,
        pincode: str,
        candidates: list[GeographicCandidate],
        state: str,
        district: str,
    ) -> Optional[dict]:
        if not pincode:
            return None

        clean_pin = normalize_pincode(pincode)
        if not clean_pin:
            return None

        pin_localities = self.gazetteer.lookup_by_pincode(clean_pin)
        if not pin_localities:
            return None

        candidate_names = {c.text.upper() for c in candidates}
        matching_pin_localities = [
            pl for pl in pin_localities
            if pl.get("office_name", "").upper() in candidate_names
            or any(
                cname in pl.get("office_name", "").upper()
                for cname in candidate_names
            )
        ]

        if len(matching_pin_localities) == 1:
            pl = matching_pin_localities[0]
            return {
                "locality": pl.get("office_name", ""),
                "latitude": pl.get("latitude"),
                "longitude": pl.get("longitude"),
                "confidence": "MEDIUM",
            }

        if pin_localities:
            pl = pin_localities[0]
            lat = pl.get("latitude")
            lon = pl.get("longitude")
            if lat is not None and lon is not None:
                return {
                    "locality": f"PIN-{clean_pin}",
                    "latitude": lat,
                    "longitude": lon,
                    "confidence": "LOW",
                }

        return None

    def _compute_locality_confidence(
        self,
        match,
        candidates: list[GeographicCandidate],
        record: UdyamCanonicalRecord,
    ) -> str:
        marker_candidates = [
            c for c in candidates
            if c.method == "admin_marker" and c.text == match.normalized_name
        ]

        if marker_candidates:
            return LocalityConfidence.HIGH.value

        if match.normalized_name in record.communication_address_normalized:
            return LocalityConfidence.MEDIUM.value

        return LocalityConfidence.LOW.value
