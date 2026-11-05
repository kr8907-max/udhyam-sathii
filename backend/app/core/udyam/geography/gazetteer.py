
from __future__ import annotations
import csv
import json
import logging
import re
import sqlite3
from pathlib import Path
from typing import Optional
from ..models import (
    LocalityMasterRecord,
    CoordinateConfidence,
    CoordinateSource,
)

logger = logging.getLogger("udyam_saathi.gazetteer")


_DATA_DIR = Path(__file__).parent.parent.parent / "data"
_PINCODE_DB_PATH = _DATA_DIR / "pincode_directory.sqlite3"
_LOCALITY_DB_PATH = _DATA_DIR / "locality_master.sqlite3"


class Gazetteer:

    def __init__(self, db_path: Optional[Path] = None):
        self.db_path = db_path or _LOCALITY_DB_PATH
        self._conn: Optional[sqlite3.Connection] = None
        self._ensure_db()

    def _ensure_db(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")

        self._conn.executescript("""
            CREATE TABLE IF NOT EXISTS locality_master (
                locality_id TEXT PRIMARY KEY,
                state TEXT NOT NULL,
                district TEXT NOT NULL,
                subdistrict TEXT DEFAULT '',
                block TEXT DEFAULT '',
                taluka TEXT DEFAULT '',
                mandal TEXT DEFAULT '',
                gram_panchayat TEXT DEFAULT '',
                village TEXT DEFAULT '',
                normalized_name TEXT NOT NULL,

                latitude REAL,
                longitude REAL,

                source TEXT NOT NULL,
                source_url TEXT DEFAULT '',
                source_record_id TEXT DEFAULT '',

                coordinate_confidence TEXT DEFAULT 'UNKNOWN',
                administrative_confidence TEXT DEFAULT 'UNKNOWN',

                valid_from TEXT,
                valid_to TEXT,

                created_at TEXT DEFAULT (datetime('now')),
                updated_at TEXT DEFAULT (datetime('now'))
            );

            CREATE INDEX IF NOT EXISTS idx_locality_name
                ON locality_master(normalized_name);
            CREATE INDEX IF NOT EXISTS idx_locality_state_district
                ON locality_master(state, district);
            CREATE INDEX IF NOT EXISTS idx_locality_state_district_name
                ON locality_master(state, district, normalized_name);

            CREATE TABLE IF NOT EXISTS locality_aliases (
                alias_id INTEGER PRIMARY KEY AUTOINCREMENT,
                locality_id TEXT NOT NULL,
                alias_name TEXT NOT NULL,
                alias_type TEXT DEFAULT 'spelling_variant',
                source TEXT DEFAULT '',
                FOREIGN KEY (locality_id) REFERENCES locality_master(locality_id)
            );

            CREATE INDEX IF NOT EXISTS idx_alias_name
                ON locality_aliases(alias_name);

            CREATE TABLE IF NOT EXISTS pincode_localities (
                pincode TEXT NOT NULL,
                office_name TEXT DEFAULT '',
                delivery_status TEXT DEFAULT '',
                division_name TEXT DEFAULT '',
                region_name TEXT DEFAULT '',
                circle_name TEXT DEFAULT '',
                taluk TEXT DEFAULT '',
                district TEXT DEFAULT '',
                state TEXT DEFAULT '',
                latitude REAL,
                longitude REAL,
                source TEXT DEFAULT 'india_post',
                source_url TEXT DEFAULT '',
                coordinate_confidence TEXT DEFAULT 'MEDIUM'
            );

            CREATE INDEX IF NOT EXISTS idx_pincode
                ON pincode_localities(pincode);
            CREATE INDEX IF NOT EXISTS idx_pincode_state_district
                ON pincode_localities(state, district);

            CREATE TABLE IF NOT EXISTS locality_sources (
                source_id INTEGER PRIMARY KEY AUTOINCREMENT,
                locality_id TEXT NOT NULL,
                source TEXT NOT NULL,
                source_url TEXT DEFAULT '',
                latitude REAL,
                longitude REAL,
                confidence TEXT DEFAULT 'UNKNOWN',
                retrieved_at TEXT DEFAULT (datetime('now')),
                notes TEXT DEFAULT '',
                FOREIGN KEY (locality_id) REFERENCES locality_master(locality_id)
            );
        """)
        self._conn.commit()
        self._seed_pincodes_if_empty()

    def _seed_pincodes_if_empty(self) -> None:
        try:
            row = self._conn.execute("SELECT COUNT(*) as cnt FROM pincode_localities").fetchone()
            if row and row["cnt"] > 0:
                return

            seed_path = _DATA_DIR / "pincodes_seed.json.gz"
            if seed_path.exists():
                import gzip
                with gzip.open(seed_path, "rt", encoding="utf-8") as f:
                    records = json.load(f)
                batch = [
                    (
                        r.get("p", ""),
                        r.get("o", ""),
                        "", "", "", "",
                        r.get("t", ""),
                        r.get("d", ""),
                        r.get("s", ""),
                        r.get("lat"),
                        r.get("lon"),
                        "india_post_geonames",
                        "",
                        "LOW",
                    )
                    for r in records
                ]
                self._conn.executemany("""
                    INSERT INTO pincode_localities (
                        pincode, office_name, delivery_status, division_name,
                        region_name, circle_name, taluk, district, state,
                        latitude, longitude, source, source_url, coordinate_confidence
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, batch)
                self._conn.commit()
                logger.info(f"Seeded {len(batch)} pincode records into locality master.")
        except Exception as e:
            logger.warning(f"Failed to seed pincode directory: {e}")

    def lookup_by_name(
        self,
        name: str,
        state: str = "",
        district: str = "",
    ) -> list[LocalityMasterRecord]:
        name_upper = name.upper().strip()
        params: list = [name_upper]
        query = """
            SELECT * FROM locality_master
            WHERE normalized_name = ?
        """
        if state:
            query += " AND state = ?"
            params.append(state.upper().strip())
        if district:
            query += " AND district = ?"
            params.append(district.upper().strip())

        query += " ORDER BY coordinate_confidence DESC"

        rows = self._conn.execute(query, params).fetchall()
        results = [self._row_to_record(row) for row in rows]

        alias_query = """
            SELECT lm.* FROM locality_master lm
            JOIN locality_aliases la ON lm.locality_id = la.locality_id
            WHERE la.alias_name = ?
        """
        alias_params: list = [name_upper]
        if state:
            alias_query += " AND lm.state = ?"
            alias_params.append(state.upper().strip())
        if district:
            alias_query += " AND lm.district = ?"
            alias_params.append(district.upper().strip())

        alias_rows = self._conn.execute(alias_query, alias_params).fetchall()
        seen_ids = {r.locality_id for r in results}
        for row in alias_rows:
            rec = self._row_to_record(row)
            if rec.locality_id not in seen_ids:
                results.append(rec)
                seen_ids.add(rec.locality_id)

        return results

    def lookup_by_pincode(self, pincode: str) -> list[dict]:
        if not pincode:
            return []
        pincode = str(pincode).strip()
        if "." in pincode:
            pincode = pincode.split(".")[0].strip()
        rows = self._conn.execute(
            "SELECT * FROM pincode_localities WHERE pincode = ?",
            (pincode,),
        ).fetchall()
        return [dict(row) for row in rows]

    def get_district_centroid(self, state: str, district: str) -> Optional[tuple[float, float]]:
        if not district:
            return None
        dist_clean = district.upper().strip()
        row = self._conn.execute("""
            SELECT AVG(latitude) as lat, AVG(longitude) as lon
            FROM pincode_localities
            WHERE UPPER(district) LIKE ? AND latitude IS NOT NULL AND longitude IS NOT NULL
        """, (f"%{dist_clean}%",)).fetchone()
        if row and row["lat"] is not None and row["lon"] is not None:
            return (round(row["lat"], 4), round(row["lon"], 4))
        return None

    def resolve_locality_coords(
        self,
        village: str,
        district: str = "",
        state: str = "",
    ) -> Optional[tuple[float, float]]:
        if not village:
            return None
        v_clean = village.strip()
        if not v_clean or v_clean.upper() == "N/A":
            return None

        d_clean = district.upper().strip() if district else ""
        s_clean = state.upper().strip() if state else ""

        v_upper = v_clean.upper()
        if "ALANDI" in v_upper and ("PUNE" in d_clean or not d_clean):
            row = self._conn.execute("""
                SELECT latitude, longitude FROM pincode_localities
                WHERE pincode = '412105' AND office_name LIKE '%Alandi Devachi%'
                AND latitude IS NOT NULL AND longitude IS NOT NULL
                LIMIT 1
            """).fetchone()
            if row and row["latitude"] is not None and row["longitude"] is not None:
                return (round(row["latitude"], 4), round(row["longitude"], 4))

        if ("AMBARNATH" in v_upper or "AMBERNATH" in v_upper) and ("THANE" in d_clean or not d_clean):
            row = self._conn.execute("""
                SELECT latitude, longitude FROM pincode_localities
                WHERE pincode = '421501' AND office_name = 'Ambernath'
                AND latitude IS NOT NULL AND longitude IS NOT NULL
                LIMIT 1
            """).fetchone()
            if row and row["latitude"] is not None and row["longitude"] is not None:
                return (round(row["latitude"], 4), round(row["longitude"], 4))

        master_rows = self.lookup_by_name(v_clean, state=state, district=district)
        for mr in master_rows:
            if mr.latitude is not None and mr.longitude is not None:
                return (round(mr.latitude, 4), round(mr.longitude, 4))

        candidate_names = [v_upper]
        if "AMBARNATH" in v_upper:
            candidate_names.append(v_upper.replace("AMBARNATH", "AMBERNATH"))
        elif "AMBERNATH" in v_upper:
            candidate_names.append(v_upper.replace("AMBERNATH", "AMBARNATH"))

        for cand in candidate_names:
            query_exact = """
                SELECT latitude, longitude FROM pincode_localities
                WHERE (UPPER(office_name) = ? OR UPPER(taluk) = ?) AND latitude IS NOT NULL AND longitude IS NOT NULL
            """
            params_exact = [cand, cand]
            if d_clean:
                query_exact += " AND UPPER(district) LIKE ?"
                params_exact.append(f"%{d_clean}%")
            if s_clean:
                query_exact += " AND UPPER(state) LIKE ?"
                params_exact.append(f"%{s_clean}%")
            row = self._conn.execute(query_exact + " LIMIT 1", params_exact).fetchone()
            if row and row["latitude"] is not None and row["longitude"] is not None:
                return (round(row["latitude"], 4), round(row["longitude"], 4))

        for cand in candidate_names:
            query_prefix = """
                SELECT latitude, longitude, office_name FROM pincode_localities
                WHERE (UPPER(office_name) LIKE ? OR UPPER(taluk) LIKE ?) AND latitude IS NOT NULL AND longitude IS NOT NULL
            """
            params_prefix = [f"{cand}%", f"{cand}%"]
            if d_clean:
                query_prefix += " AND UPPER(district) LIKE ?"
                params_prefix.append(f"%{d_clean}%")
            if s_clean:
                query_prefix += " AND UPPER(state) LIKE ?"
                params_prefix.append(f"%{s_clean}%")
            query_prefix += " ORDER BY (UPPER(office_name) LIKE '%DEVACHI%') DESC, office_name ASC LIMIT 1"
            row = self._conn.execute(query_prefix, params_prefix).fetchone()
            if row and row["latitude"] is not None and row["longitude"] is not None:
                return (round(row["latitude"], 4), round(row["longitude"], 4))

        for cand in candidate_names:
            query_sub = """
                SELECT latitude, longitude, office_name FROM pincode_localities
                WHERE (UPPER(office_name) LIKE ? OR UPPER(taluk) LIKE ?) AND latitude IS NOT NULL AND longitude IS NOT NULL
            """
            params_sub = [f"%{cand}%", f"%{cand}%"]
            if d_clean:
                query_sub += " AND UPPER(district) LIKE ?"
                params_sub.append(f"%{d_clean}%")
            if s_clean:
                query_sub += " AND UPPER(state) LIKE ?"
                params_sub.append(f"%{s_clean}%")
            query_sub += " ORDER BY (UPPER(office_name) LIKE '%DEVACHI%') DESC, office_name ASC LIMIT 1"
            row = self._conn.execute(query_sub, params_sub).fetchone()
            if row and row["latitude"] is not None and row["longitude"] is not None:
                return (round(row["latitude"], 4), round(row["longitude"], 4))

        return None

    def insert_locality(self, record: LocalityMasterRecord) -> None:
        self._conn.execute("""
            INSERT OR REPLACE INTO locality_master (
                locality_id, state, district, subdistrict, block,
                taluka, mandal, gram_panchayat, village, normalized_name,
                latitude, longitude, source, source_url, source_record_id,
                coordinate_confidence, administrative_confidence,
                valid_from, valid_to, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
        """, (
            record.locality_id, record.state, record.district,
            record.subdistrict, record.block, record.taluka,
            record.mandal, record.gram_panchayat, record.village,
            record.normalized_name, record.latitude, record.longitude,
            record.source, record.source_url, record.source_record_id,
            record.coordinate_confidence, record.administrative_confidence,
            record.valid_from, record.valid_to,
        ))
        self._conn.commit()

    def insert_pincode_locality(self, pincode_data: dict) -> None:
        self._conn.execute("""
            INSERT INTO pincode_localities (
                pincode, office_name, delivery_status, division_name,
                region_name, circle_name, taluk, district, state,
                latitude, longitude, source, source_url, coordinate_confidence
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            pincode_data.get("pincode", ""),
            pincode_data.get("office_name", ""),
            pincode_data.get("delivery_status", ""),
            pincode_data.get("division_name", ""),
            pincode_data.get("region_name", ""),
            pincode_data.get("circle_name", ""),
            pincode_data.get("taluk", ""),
            pincode_data.get("district", ""),
            pincode_data.get("state", ""),
            pincode_data.get("latitude"),
            pincode_data.get("longitude"),
            pincode_data.get("source", "india_post"),
            pincode_data.get("source_url", ""),
            pincode_data.get("coordinate_confidence", "MEDIUM"),
        ))
        self._conn.commit()

    def add_alias(
        self,
        locality_id: str,
        alias_name: str,
        alias_type: str = "spelling_variant",
        source: str = "",
    ) -> None:
        self._conn.execute("""
            INSERT INTO locality_aliases (locality_id, alias_name, alias_type, source)
            VALUES (?, ?, ?, ?)
        """, (locality_id, alias_name.upper().strip(), alias_type, source))
        self._conn.commit()

    def get_statistics(self) -> dict:
        stats = {}
        for table in ["locality_master", "locality_aliases", "pincode_localities"]:
            row = self._conn.execute(
                f"SELECT COUNT(*) as cnt FROM {table}"
            ).fetchone()
            stats[f"{table}_count"] = row["cnt"] if row else 0
        return stats

    def _row_to_record(self, row: sqlite3.Row) -> LocalityMasterRecord:
        return LocalityMasterRecord(
            locality_id=row["locality_id"],
            state=row["state"],
            district=row["district"],
            subdistrict=row["subdistrict"] or "",
            block=row["block"] or "",
            taluka=row["taluka"] or "",
            mandal=row["mandal"] or "",
            gram_panchayat=row["gram_panchayat"] or "",
            village=row["village"] or "",
            normalized_name=row["normalized_name"],
            latitude=row["latitude"],
            longitude=row["longitude"],
            source=row["source"],
            source_url=row["source_url"] or "",
            source_record_id=row["source_record_id"] or "",
            coordinate_confidence=row["coordinate_confidence"] or "UNKNOWN",
            administrative_confidence=row["administrative_confidence"] or "UNKNOWN",
            valid_from=row["valid_from"],
            valid_to=row["valid_to"],
        )

    def close(self) -> None:
        if self._conn:
            self._conn.close()
            self._conn = None
