
from __future__ import annotations
import asyncio
import json
import logging
import os
import time
import hashlib
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Any
from dataclasses import dataclass, field

import httpx

from .models import (
    UdyamRawRecord,
    UdyamCanonicalRecord,
    PipelineRunMetadata,
    raw_to_canonical,
)

logger = logging.getLogger("udyam_saathi.udyam_client")


RESOURCE_ID = "8b68ae56-84cf-4728-a0a6-1be11028dea7"
API_BASE_URL = "https://api.data.gov.in/resource"

EXPECTED_FIELDS = {
    "State", "District", "Pincode", "RegistrationDate",
    "EnterpriseName", "CommunicationAddress", "Activities",
}

OPTIONAL_FIELDS = {
    "LG_ST_Code", "LG_DT_Code",
}

DEFAULT_PAGE_SIZE = 500
MAX_PAGE_SIZE = 5000
MAX_PAGES = 20

MAX_RECORDS_HARD_CAP = 500

CIRCUIT_BREAKER_COOLDOWN_SECONDS = 300


@dataclass
class RetrievalMetadata:
    resource_id: str = RESOURCE_ID
    retrieved_at: str = ""
    state_filter: str = ""
    district_filter: str = ""
    pincode_filter: str = ""
    offset: int = 0
    limit: int = DEFAULT_PAGE_SIZE
    api_total: int = 0
    records_received: int = 0
    pages_fetched: int = 0
    http_status: int = 0
    duration_seconds: float = 0.0
    errors: list = field(default_factory=list)
    warnings: list = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "resource_id": self.resource_id,
            "retrieved_at": self.retrieved_at,
            "state": self.state_filter,
            "district": self.district_filter,
            "pincode": self.pincode_filter,
            "offset": self.offset,
            "limit": self.limit,
            "api_total": self.api_total,
            "records_received": self.records_received,
            "pages_fetched": self.pages_fetched,
            "http_status": self.http_status,
            "duration_seconds": self.duration_seconds,
            "errors": self.errors,
            "warnings": self.warnings,
        }


class UdyamClient:

    def __init__(
        self,
        api_key: Optional[str] = None,
        cache_dir: Optional[Path] = None,
        page_size: int = DEFAULT_PAGE_SIZE,
        max_retries: int = 2,
        retry_backoff_base: float = 2.0,
        request_timeout: int = 30,
        rate_limit_delay: float = 0.5,
    ):
        if not api_key:
            api_key = os.getenv("DATA_GOV_IN_API_KEY", "")
        if not api_key:
            try:
                from dotenv import load_dotenv
                load_dotenv()
                api_key = os.getenv("DATA_GOV_IN_API_KEY", "")
            except Exception:
                pass
        self.api_key = api_key
        if not self.api_key:
            raise ValueError(
                "UDYAM API key is required. Set DATA_GOV_IN_API_KEY "
                "environment variable or pass api_key parameter."
            )

        self.resource_id = RESOURCE_ID
        self.base_url = f"{API_BASE_URL}/{self.resource_id}"
        self.page_size = min(page_size, MAX_PAGE_SIZE)
        self.max_retries = max_retries
        self.retry_backoff_base = retry_backoff_base
        self.request_timeout = request_timeout
        self.rate_limit_delay = rate_limit_delay

        if cache_dir is None:
            cache_dir = Path(__file__).parent.parent / "data" / "raw" / "udyam"
        self.cache_dir = cache_dir
        self.cache_dir.mkdir(parents=True, exist_ok=True)

        self._client: Optional[httpx.AsyncClient] = None

        if not hasattr(UdyamClient, '_circuit_open_until'):
            UdyamClient._circuit_open_until = 0.0
            UdyamClient._circuit_failure_count = 0
            UdyamClient._last_successful_fetch = 0.0

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                headers={
                    "User-Agent": "UdyamSaathi/2.0",
                    "Accept": "application/json",
                },
                timeout=httpx.Timeout(self.request_timeout, connect=10.0),
            )
        return self._client

    async def close(self):
        if self._client and not self._client.is_closed:
            await self._client.aclose()
            self._client = None

    async def fetch_by_district(
        self,
        state: str,
        district: str,
        max_records: Optional[int] = None,
    ) -> tuple[list[UdyamCanonicalRecord], RetrievalMetadata]:
        filters = {
            "State": state.upper(),
            "District": district.upper(),
        }
        return await self._paginated_fetch(
            filters=filters,
            max_records=max_records,
            state_label=state,
            district_label=district,
        )

    async def fetch_by_pincode(
        self,
        pincode: str,
        max_records: Optional[int] = None,
    ) -> tuple[list[UdyamCanonicalRecord], RetrievalMetadata]:
        filters = {
            "Pincode": str(pincode).strip(),
        }
        return await self._paginated_fetch(
            filters=filters,
            max_records=max_records,
            pincode_label=pincode,
        )

    async def fetch_by_state_district_pincode(
        self,
        state: str,
        district: str,
        pincode: str,
        max_records: Optional[int] = None,
    ) -> tuple[list[UdyamCanonicalRecord], RetrievalMetadata]:
        filters = {
            "State": state.upper(),
            "District": district.upper(),
            "Pincode": str(pincode).strip(),
        }
        return await self._paginated_fetch(
            filters=filters,
            max_records=max_records,
            state_label=state,
            district_label=district,
            pincode_label=pincode,
        )

    async def _paginated_fetch(
        self,
        filters: dict[str, str],
        max_records: Optional[int] = None,
        state_label: str = "",
        district_label: str = "",
        pincode_label: str = "",
    ) -> tuple[list[UdyamCanonicalRecord], RetrievalMetadata]:
        effective_max = min(
            max_records or MAX_RECORDS_HARD_CAP,
            MAX_RECORDS_HARD_CAP,
        )

        now_mono = time.monotonic()
        if now_mono < UdyamClient._circuit_open_until:
            remaining = UdyamClient._circuit_open_until - now_mono
            logger.warning(
                f"[UDYAM] Circuit breaker OPEN — skipping API call. "
                f"data.gov.in failed {UdyamClient._circuit_failure_count}x recently. "
                f"Retry in {remaining:.0f}s"
            )
            metadata = RetrievalMetadata(
                retrieved_at=datetime.now(timezone.utc).isoformat(),
                state_filter=state_label,
                district_filter=district_label,
                pincode_filter=pincode_label,
                limit=self.page_size,
            )
            metadata.errors.append(
                f"Circuit breaker open: data.gov.in API unavailable. "
                f"Retry in {remaining:.0f}s"
            )
            metadata.duration_seconds = 0.0
            return [], metadata

        start_time = time.monotonic()
        metadata = RetrievalMetadata(
            retrieved_at=datetime.now(timezone.utc).isoformat(),
            state_filter=state_label,
            district_filter=district_label,
            pincode_filter=pincode_label,
            limit=self.page_size,
        )

        all_records: list[UdyamCanonicalRecord] = []
        seen_fingerprints: set[str] = set()
        offset = 0
        pages_fetched = 0
        first_page_total: Optional[int] = None
        schema_validated = False

        while pages_fetched < MAX_PAGES:
            if pages_fetched > 0:
                await asyncio.sleep(self.rate_limit_delay)

            params = {
                "api-key": self.api_key,
                "format": "json",
                "limit": self.page_size,
                "offset": offset,
            }
            for filter_key, filter_val in filters.items():
                params[f"filters[{filter_key}]"] = filter_val

            response_data = await self._request_with_retry(params, metadata)
            if response_data is None:
                break

            pages_fetched += 1
            metadata.pages_fetched = pages_fetched

            api_total = int(response_data.get("total", 0))
            metadata.api_total = api_total

            if first_page_total is None:
                first_page_total = api_total
            elif api_total != first_page_total:
                metadata.warnings.append(
                    f"API total changed during retrieval: "
                    f"{first_page_total} -> {api_total} (page {pages_fetched})"
                )

            records_list = response_data.get("records", [])
            if not schema_validated and records_list:
                self._validate_schema(records_list[0], metadata)
                schema_validated = True

            page_new_count = 0
            for raw_dict in records_list:
                raw_record = self._dict_to_raw_record(raw_dict, metadata)
                canonical = raw_to_canonical(raw_record)

                if canonical.record_fingerprint in seen_fingerprints:
                    metadata.warnings.append(
                        f"Duplicate record detected: {canonical.enterprise_name_raw[:50]}"
                    )
                    continue

                seen_fingerprints.add(canonical.record_fingerprint)
                all_records.append(canonical)
                page_new_count += 1

                if len(all_records) >= effective_max:
                    metadata.warnings.append(
                        f"Hard cap reached: {effective_max} records "
                        f"(api_total={api_total})"
                    )
                    break

            metadata.records_received = len(all_records)

            logger.info(
                f"[UDYAM] Page {pages_fetched}: offset={offset}, "
                f"received={len(records_list)}, new={page_new_count}, "
                f"total_so_far={len(all_records)}, api_total={api_total}"
            )

            if len(all_records) >= effective_max:
                break

            if len(records_list) < self.page_size:
                break

            if len(all_records) >= api_total:
                break

            offset += self.page_size

        self._cache_results(all_records, metadata, filters)

        metadata.duration_seconds = time.monotonic() - start_time

        if len(all_records) > 0:
            UdyamClient._circuit_open_until = 0.0
            UdyamClient._circuit_failure_count = 0
            UdyamClient._last_successful_fetch = time.monotonic()
            logger.info(
                f"[UDYAM] Circuit breaker CLOSED — API healthy"
            )
        elif metadata.errors:
            UdyamClient._circuit_failure_count += 1
            UdyamClient._circuit_open_until = (
                time.monotonic() + CIRCUIT_BREAKER_COOLDOWN_SECONDS
            )
            logger.warning(
                f"[UDYAM] Circuit breaker OPEN — data.gov.in unavailable. "
                f"Failures: {UdyamClient._circuit_failure_count}. "
                f"Next retry in {CIRCUIT_BREAKER_COOLDOWN_SECONDS}s"
            )

        logger.info(
            f"[UDYAM] Retrieval complete: {len(all_records)} unique records "
            f"in {metadata.duration_seconds:.2f}s ({pages_fetched} pages)"
        )

        return all_records, metadata

    async def _request_with_retry(
        self,
        params: dict,
        metadata: RetrievalMetadata,
    ) -> Optional[dict]:
        client = await self._get_client()

        for attempt in range(self.max_retries):
            try:
                resp = await client.get(
                    self.base_url,
                    params=params,
                )
                metadata.http_status = resp.status_code

                if resp.status_code == 200:
                    return resp.json()

                if resp.status_code == 429:
                    wait = self.retry_backoff_base ** (attempt + 2)
                    metadata.warnings.append(
                        f"Rate limited (429) on attempt {attempt+1}, "
                        f"waiting {wait:.1f}s"
                    )
                    logger.warning(
                        f"[UDYAM] Rate limited. Waiting {wait:.1f}s"
                    )
                    await asyncio.sleep(wait)
                    continue

                if resp.status_code == 502:
                    wait = self.retry_backoff_base ** (attempt + 1)
                    metadata.warnings.append(
                        f"Bad Gateway (502) on attempt {attempt+1}, "
                        f"waiting {wait:.1f}s"
                    )
                    logger.warning(
                        f"[UDYAM] Bad Gateway (502). Server-side issue. "
                        f"Retrying in {wait:.1f}s"
                    )
                    await asyncio.sleep(wait)
                    continue

                resp_text = resp.text[:200]
                metadata.errors.append(
                    f"HTTP {resp.status_code}: {resp_text}"
                )
                logger.error(
                    f"[UDYAM] HTTP {resp.status_code}: {resp_text}"
                )

                if resp.status_code >= 500:
                    wait = self.retry_backoff_base ** (attempt + 1)
                    await asyncio.sleep(wait)
                    continue
                else:
                    return None

            except httpx.TimeoutException:
                wait = self.retry_backoff_base ** (attempt + 1)
                metadata.warnings.append(
                    f"Timeout on attempt {attempt+1}, waiting {wait:.1f}s"
                )
                logger.warning(
                    f"[UDYAM] Request timeout. Retrying in {wait:.1f}s"
                )
                await asyncio.sleep(wait)

            except httpx.ConnectError as e:
                wait = self.retry_backoff_base ** (attempt + 1)
                metadata.errors.append(
                    f"Connection error on attempt {attempt+1}: {str(e)[:100]}"
                )
                logger.error(f"[UDYAM] Connection error: {e}")
                await asyncio.sleep(wait)

            except Exception as e:
                metadata.errors.append(
                    f"Unexpected error on attempt {attempt+1}: "
                    f"{type(e).__name__}: {str(e)[:100]}"
                )
                logger.error(f"[UDYAM] Unexpected error: {e}", exc_info=True)
                return None

        metadata.errors.append(
            f"All {self.max_retries} retry attempts exhausted"
        )
        logger.error(f"[UDYAM] All {self.max_retries} retries exhausted")
        return None

    def _validate_schema(
        self,
        sample_record: dict,
        metadata: RetrievalMetadata,
    ) -> None:
        actual_keys = set(sample_record.keys())

        missing = EXPECTED_FIELDS - actual_keys
        if missing:
            metadata.warnings.append(
                f"Missing expected fields: {missing}"
            )
            logger.warning(f"[UDYAM] Missing expected fields: {missing}")

        known = EXPECTED_FIELDS | OPTIONAL_FIELDS
        unexpected = actual_keys - known
        if unexpected:
            metadata.warnings.append(
                f"Unexpected new fields detected: {unexpected}"
            )
            logger.info(f"[UDYAM] New fields detected: {unexpected}")

    def _dict_to_raw_record(
        self,
        raw_dict: dict,
        metadata: RetrievalMetadata,
    ) -> UdyamRawRecord:
        ci = {k.lower(): v for k, v in raw_dict.items()}

        return UdyamRawRecord(
            lg_st_code=ci.get("lg_st_code", ci.get("lgstcode", "")),
            state=ci.get("state", ""),
            lg_dt_code=ci.get("lg_dt_code", ci.get("lgdtcode", "")),
            district=ci.get("district", ""),
            pincode=str(ci.get("pincode", "")),
            registration_date=ci.get("registrationdate", ci.get("registration_date", "")),
            enterprise_name=ci.get("enterprisename", ci.get("enterprise_name", "")),
            communication_address=ci.get(
                "communicationaddress",
                ci.get("communication_address", "")
            ),
            activities=ci.get("activities", ""),
            retrieved_at=metadata.retrieved_at,
        )

    def _cache_results(
        self,
        records: list[UdyamCanonicalRecord],
        metadata: RetrievalMetadata,
        filters: dict[str, str],
    ) -> None:
        try:
            now = datetime.now(timezone.utc)
            date_dir = self.cache_dir / now.strftime("%Y") / now.strftime("%m") / now.strftime("%d")
            date_dir.mkdir(parents=True, exist_ok=True)

            filter_str = json.dumps(filters, sort_keys=True)
            filter_hash = hashlib.md5(filter_str.encode()).hexdigest()[:12]
            cache_file = date_dir / f"{filter_hash}.json"

            cache_data = {
                "metadata": metadata.to_dict(),
                "filters": filters,
                "record_count": len(records),
                "records": [r.to_dict() for r in records[:50]],
                "all_fingerprints": [r.record_fingerprint for r in records],
            }

            cache_file.write_text(
                json.dumps(cache_data, indent=2, ensure_ascii=False, default=str),
                encoding="utf-8",
            )
            logger.info(f"[UDYAM] Cached to {cache_file}")

        except Exception as e:
            logger.warning(f"[UDYAM] Cache write failed: {e}")
