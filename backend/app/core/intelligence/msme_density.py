
from __future__ import annotations

import logging
from collections import defaultdict
from typing import Any, Optional

logger = logging.getLogger("udyam_saathi.msme_density")


def _locality_key(biz: dict[str, Any]) -> str:
    village = (biz.get("village") or biz.get("resolved_locality") or "").strip().upper()
    pincode = str(biz.get("pincode") or "").strip()
    if "." in pincode:
        pincode = pincode.split(".")[0].strip()

    if village and pincode:
        return f"{village}|{pincode}"
    if village:
        return f"{village}|"
    if pincode:
        return f"|{pincode}"
    return "|UNKNOWN|"


def aggregate_village_density(
    nodes: list[dict[str, Any]],
    unmapped: list[dict[str, Any]],
    center_lat: Optional[float] = None,
    center_lon: Optional[float] = None,
) -> list[dict[str, Any]]:
    village_groups: dict[str, list[dict]] = defaultdict(list)

    for node in nodes:
        village = (
            node.get("address", "")
            or f"{node.get('district', '')}, {node.get('state', '')}"
        ).strip()
        pincode = str(node.get("pincode", "")).strip()

        village_name = _extract_village_name(node)
        group_key = f"{village_name.upper()}|{pincode}" if village_name else f"|{pincode}"

        village_groups[group_key].append(node)

    unmapped_counts: dict[str, int] = defaultdict(int)
    for entity in unmapped:
        village_name = _extract_village_name(entity)
        pincode = str(entity.get("pincode", "")).strip()
        group_key = f"{village_name.upper()}|{pincode}" if village_name else f"|{pincode}"
        unmapped_counts[group_key] += 1

    density_points = []
    for group_key, group_nodes in village_groups.items():
        if not group_nodes:
            continue

        parts = group_key.split("|", 1)
        village_display = parts[0].title() if parts[0] else "Unknown Locality"
        pincode = parts[1] if len(parts) > 1 else ""

        valid_lats = [n["location"]["latitude"] for n in group_nodes
                      if n.get("location", {}).get("latitude") is not None]
        valid_lons = [n["location"]["longitude"] for n in group_nodes
                      if n.get("location", {}).get("longitude") is not None]

        if not valid_lats or not valid_lons:
            continue

        rep_lat = sum(valid_lats) / len(valid_lats)
        rep_lon = sum(valid_lons) / len(valid_lons)

        precisions = [n.get("location", {}).get("precision", "UNMAPPED")
                      for n in group_nodes]
        precision_rank = {"EXACT": 0, "LOCALITY": 1, "VILLAGE": 2, "PINCODE": 3, "UNMAPPED": 4}
        best_precision = min(precisions, key=lambda p: precision_rank.get(p, 4))

        sector_counts: dict[str, int] = defaultdict(int)
        for node in group_nodes:
            sector = node.get("sector", "UNKNOWN")
            sector_counts[sector] += 1

        mapped_count = len(group_nodes)
        unmapped_count = unmapped_counts.get(group_key, 0)

        density_points.append({
            "villageName": village_display,
            "latitude": round(rep_lat, 6),
            "longitude": round(rep_lon, 6),
            "msmeCount": mapped_count + unmapped_count,
            "mappedCount": mapped_count,
            "unmappedCount": unmapped_count,
            "pincode": pincode,
            "precision": best_precision,
            "sectorBreakdown": dict(sector_counts),
        })

    density_points.sort(key=lambda d: d["msmeCount"], reverse=True)

    max_count = max((d["msmeCount"] for d in density_points), default=1)
    for dp in density_points:
        dp["intensityNormalized"] = round(dp["msmeCount"] / max(max_count, 1), 4)

    logger.info(
        f"Aggregated {sum(d['msmeCount'] for d in density_points)} MSMEs "
        f"across {len(density_points)} village-level clusters."
    )

    return density_points


def _extract_village_name(record: dict[str, Any]) -> str:
    address = record.get("address", "")
    if address:
        parts = [p.strip() for p in address.split(",") if p.strip()]
        if parts:
            return parts[0]

    district = record.get("district", "")
    if district:
        return district

    return ""
