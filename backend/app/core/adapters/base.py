
from __future__ import annotations
from abc import ABC, abstractmethod
from dataclasses import dataclass, asdict, field
from typing import Optional, Any
from datetime import datetime, timezone


@dataclass
class DatasetMetadata:
    source_id: str
    dataset_name: str
    publisher: str
    url: str = ""
    coverage: str = "National"
    geographic_level: str = "village"
    reference_period: str = ""
    license: str = "Open Government Data (OGD) License - India"
    last_verified: str = field(default_factory=lambda: datetime.now(timezone.utc).strftime("%Y-%m-%d"))
    schema_version: str = "1.0"
    notes: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


class GovernmentDatasetAdapter(ABC):

    @abstractmethod
    def metadata(self) -> DatasetMetadata:
        pass

    @abstractmethod
    def fetch_features(
        self,
        state: str,
        district: str,
        village: str = "",
        pincode: Optional[str] = None,
    ) -> dict[str, Any]:
        pass
