
from __future__ import annotations
import json
import re
import logging
from typing import Optional
from ..models import ActivityRecord

logger = logging.getLogger("udyam_saathi.activities")


def parse_activities(raw_activities: str) -> list[ActivityRecord]:
    if not raw_activities or not raw_activities.strip():
        return []

    raw = raw_activities.strip()

    activities = _try_json_parse(raw)
    if activities:
        return activities

    activities = _try_delimited_parse(raw, ";")
    if activities:
        return activities
    activities = _try_delimited_parse(raw, "|")
    if activities:
        return activities

    return _parse_single_activity(raw)


def _try_json_parse(raw: str) -> list[ActivityRecord]:
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        try:
            fixed = raw.replace("'", '"')
            fixed = re.sub(r',\s*}', '}', fixed)
            fixed = re.sub(r',\s*]', ']', fixed)
            data = json.loads(fixed)
        except (json.JSONDecodeError, ValueError):
            return []

    records = []

    if isinstance(data, list):
        for item in data:
            if isinstance(item, dict):
                records.extend(_extract_from_dict(item))
            elif isinstance(item, str):
                records.extend(_parse_single_activity(item))
    elif isinstance(data, dict):
        records.extend(_extract_from_dict(data))
    elif isinstance(data, str):
        records.extend(_parse_single_activity(data))

    return records


def _extract_from_dict(d: dict) -> list[ActivityRecord]:
    records = []

    nic_keys = [
        "nic5digitid", "nic4digitid", "nic2digitid",
        "nic_2_digit", "nic_code", "nic", "nic_code",
        "nic_4_digit", "nic_5_digit", "nic5digit", "nic4digit", "nic2digit",
    ]
    desc_keys = [
        "activity", "description", "activity_description",
        "nic_description", "name",
    ]

    nic_code = ""
    description = ""

    d_lower = {str(k).lower(): v for k, v in d.items()}

    for key in nic_keys:
        if key in d_lower and d_lower[key] is not None:
            nic_code = str(d_lower[key]).strip()
            break

    for key in desc_keys:
        if key in d_lower and d_lower[key] is not None:
            description = str(d_lower[key]).strip()
            break

    if nic_code or description:
        rec = ActivityRecord(
            nic_code=nic_code,
            nic_description_raw=description,
            activity_description=description,
            normalized_category="UNKNOWN",
            category_confidence="UNKNOWN",
        )
        try:
            from .categories import enrich_activity_record
            rec = enrich_activity_record(rec)
        except Exception:
            pass
        records.append(rec)
    else:
        for key, value in d.items():
            val = str(value).strip()
            if val and len(val) > 2:
                if re.match(r'^\d{2,5}$', val):
                    nic_code = val
                else:
                    rec = ActivityRecord(
                        nic_code="",
                        nic_description_raw=val,
                        activity_description=val,
                        normalized_category="UNKNOWN",
                        category_confidence="UNKNOWN",
                    )
                    try:
                        from .categories import enrich_activity_record
                        rec = enrich_activity_record(rec)
                    except Exception:
                        pass
                    records.append(rec)

    return records


def _try_delimited_parse(raw: str, delimiter: str) -> list[ActivityRecord]:
    parts = raw.split(delimiter)
    if len(parts) <= 1:
        return []

    records = []
    for part in parts:
        part = part.strip()
        if part:
            records.extend(_parse_single_activity(part))

    return records if records else []


def _parse_single_activity(text: str) -> list[ActivityRecord]:
    text = text.strip()
    if not text:
        return []

    nic_code = ""
    description = text

    nic_patterns = [
        re.compile(r'^(\d{2,5})\s*[-:]\s*(.+)$'),
        re.compile(r'^NIC\s*[-:]?\s*(\d{2,5})\s*[-:]?\s*(.+)$', re.IGNORECASE),
        re.compile(r'^(\d{2,5})\s+(.+)$'),
    ]

    for pattern in nic_patterns:
        match = pattern.match(text)
        if match:
            nic_code = match.group(1).strip()
            description = match.group(2).strip()
            break

    return [ActivityRecord(
        nic_code=nic_code,
        nic_description_raw=text,
        activity_description=description,
        normalized_category="UNKNOWN",
        category_confidence="UNKNOWN",
    )]
