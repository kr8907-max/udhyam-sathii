
from __future__ import annotations
import re
from typing import Optional
from ..models import GeographicCandidate


_ADMIN_MARKERS = {
    "VILLAGE": "village",
    "VILL": "village",
    "GRAM": "village",

    "HAMLET": "hamlet",
    "TOLA": "hamlet",
    "MAJRA": "hamlet",
    "PADA": "hamlet",
    "WADI": "hamlet",

    "WARD": "ward",

    "TOWN": "town",
    "NAGAR": "town",
    "PURA": "town",
    "CITY": "town",

    "TALUKA": "taluka",
    "TALUK": "taluka",
    "TEHSIL": "tehsil",
    "MANDAL": "mandal",
    "MDL": "mandal",

    "BLOCK": "block",
    "BLK": "block",

    "GRAM PANCHAYAT": "gram_panchayat",
    "GP": "gram_panchayat",
    "PANCHAYAT": "gram_panchayat",

    "POST OFFICE": "post_office",
    "POST": "post_office",
    "PO": "post_office",

    "DISTRICT": "district",
    "DIST": "district",
    "DT": "district",

    "STATE": "state",
}


_NOISE_TOKENS = {
    "NEAR", "BEHIND", "BESIDE", "OPPOSITE", "FRONT", "BACK",
    "FLOOR", "PLOT", "SURVEY", "GAT", "KHASRA", "KHATA",
    "SHOP", "HOUSE", "FLAT", "ROOM", "BUILDING",
    "ROAD", "STREET", "LANE", "NAGAR", "COLONY", "LAYOUT",
    "CROSS", "MAIN", "BYE", "PASS",
    "INDUSTRIAL", "ESTATE", "AREA", "ZONE", "COMPLEX",
    "SHED", "GODOWN", "FACTORY", "UNIT", "WORKS",
    "NORTH", "SOUTH", "EAST", "WEST",
    "NEW", "OLD", "BIG", "SMALL",
    "NO", "NUMBER",
    "AND", "THE", "OF", "AT", "IN", "TO",
    "H", "C", "O",
}


_NUMBER_PATTERNS = [
    re.compile(r'^\d+$'),
    re.compile(r'^\d+[-/]\d+'),
    re.compile(r'^\d+[A-Z]?$'),
    re.compile(r'^[A-Z]\d+$'),
]


def extract_geographic_candidates(
    normalized_address: str,
    known_state: str = "",
    known_district: str = "",
    known_pincode: str = "",
) -> list[GeographicCandidate]:
    if not normalized_address:
        return []

    candidates: list[GeographicCandidate] = []
    tokens = normalized_address.split()

    candidates.extend(
        _extract_by_admin_markers(tokens, normalized_address)
    )

    candidates.extend(
        _extract_by_position(tokens, known_state, known_district)
    )

    if known_district:
        candidates.append(GeographicCandidate(
            text=known_district.upper(),
            entity_type="district",
            position=-1,
            method="api_field",
            confidence="HIGH",
        ))

    if known_pincode:
        candidates.append(GeographicCandidate(
            text=known_pincode,
            entity_type="pincode",
            position=-1,
            method="api_field",
            confidence="HIGH",
        ))

    seen = set()
    unique_candidates = []
    for c in candidates:
        key = (c.text, c.entity_type)
        if key not in seen:
            seen.add(key)
            unique_candidates.append(c)

    return unique_candidates


def _extract_by_admin_markers(
    tokens: list[str],
    full_address: str,
) -> list[GeographicCandidate]:
    candidates = []

    for i, token in enumerate(tokens):
        if token in _ADMIN_MARKERS and i + 1 < len(tokens):
            next_token = tokens[i + 1]
            if not _is_noise_or_number(next_token):
                entity_type = _ADMIN_MARKERS[token]
                pos = full_address.find(next_token)
                candidates.append(GeographicCandidate(
                    text=next_token,
                    entity_type=entity_type,
                    position=pos if pos >= 0 else i + 1,
                    method="admin_marker",
                    confidence="MEDIUM",
                ))

        if i + 2 < len(tokens):
            two_token = f"{token} {tokens[i + 1]}"
            if two_token in _ADMIN_MARKERS:
                next_token = tokens[i + 2]
                if not _is_noise_or_number(next_token):
                    entity_type = _ADMIN_MARKERS[two_token]
                    pos = full_address.find(next_token)
                    candidates.append(GeographicCandidate(
                        text=next_token,
                        entity_type=entity_type,
                        position=pos if pos >= 0 else i + 2,
                        method="admin_marker",
                        confidence="MEDIUM",
                    ))

    return candidates


def _extract_by_position(
    tokens: list[str],
    known_state: str,
    known_district: str,
) -> list[GeographicCandidate]:
    candidates = []
    known_upper = {
        known_state.upper(),
        known_district.upper(),
    }
    known_upper.discard("")

    place_tokens = []
    for i, token in enumerate(tokens):
        if _is_noise_or_number(token):
            continue
        if token in _ADMIN_MARKERS:
            continue
        if token in known_upper:
            continue
        if len(token) < 2:
            continue
        place_tokens.append((i, token))

    for idx, (original_pos, token) in enumerate(place_tokens):
        if idx < len(place_tokens) // 2 + 1:
            entity_type = "locality"
        else:
            entity_type = "locality"

        candidates.append(GeographicCandidate(
            text=token,
            entity_type=entity_type,
            position=original_pos,
            method="positional_heuristic",
            confidence="LOW",
        ))

    return candidates


def _is_noise_or_number(token: str) -> bool:
    if token in _NOISE_TOKENS:
        return True
    for pattern in _NUMBER_PATTERNS:
        if pattern.match(token):
            return True
    return False
