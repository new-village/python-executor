"""
pipeline_utils.py

Shared utilities for the corpreg NTA monthly/daily pipelines:
- GCS helpers (download, upload)
- Parse helpers (safe wrappers around ja-entity-parser)
- Chunk parse function
- Merge logic
- Closed-company extraction

All GCS operations go through local temp files to keep memory bounded.
"""

from __future__ import annotations

import gc
import logging
import tempfile
from pathlib import Path

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
from google.cloud import storage

from ja_entityparser import parse_corporate, parse_address

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
GCS_RAW_BUCKET = "yata-raw"
GCS_MASTER_BUCKET = "yata-master"
PROJECT_ID = "yata-intelligence"

CHUNK_SIZE = 50_000
LOG_EVERY = 200_000

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

# Parsed columns added by ja-entity-parser
PARSED_NAME_COLS = [
    "parsed_legal_form",
    "parsed_brand_name",
    "parsed_brand_kana",
    "parsed_name_normalized",
]
PARSED_ADDR_COLS = [
    "parsed_state",
    "parsed_city",
    "parsed_suburb",
    "parsed_house_number",
    "parsed_house_number_raw",
    "parsed_addr_normalized",
]
PARSED_FIELDS = [
    pa.field(c, pa.string()) for c in PARSED_NAME_COLS + PARSED_ADDR_COLS
]


# ---------------------------------------------------------------------------
# GCS helpers
# ---------------------------------------------------------------------------

def gcs_client() -> storage.Client:
    return storage.Client(project=PROJECT_ID)


def download_to_tmpfile(
    sc: storage.Client, bucket_name: str, blob_name: str
) -> Path:
    """Stream-download a GCS blob to a local temp file; return its Path."""
    tmp = tempfile.NamedTemporaryFile(suffix=".parquet", delete=False)
    tmp_path = Path(tmp.name)
    tmp.close()
    logger.info(f"Downloading gs://{bucket_name}/{blob_name} → {tmp_path} ...")
    bucket = sc.bucket(bucket_name)
    blob = bucket.blob(blob_name)
    blob.download_to_filename(str(tmp_path))
    size_mb = tmp_path.stat().st_size / 1024 / 1024
    logger.info(f"  Downloaded {size_mb:.1f} MB")
    return tmp_path


def upload_from_tmpfile(
    sc: storage.Client, local_path: Path, bucket_name: str, blob_name: str
) -> None:
    """Upload a local file to GCS."""
    size_mb = local_path.stat().st_size / 1024 / 1024
    logger.info(
        f"Uploading {local_path} ({size_mb:.1f} MB) → gs://{bucket_name}/{blob_name} ..."
    )
    bucket = sc.bucket(bucket_name)
    blob = bucket.blob(blob_name)
    blob.upload_from_filename(str(local_path), content_type="application/octet-stream")
    logger.info("  Upload complete.")


def blob_exists(sc: storage.Client, bucket_name: str, blob_name: str) -> bool:
    """Check whether a GCS blob exists."""
    bucket = sc.bucket(bucket_name)
    return bucket.blob(blob_name).exists()


# ---------------------------------------------------------------------------
# Parse helpers (single-row, error-safe)
# ---------------------------------------------------------------------------

def safe_parse_corporate(name: str) -> dict:
    if not name or not isinstance(name, str):
        return {}
    try:
        return parse_corporate(name)
    except Exception:
        return {}


def safe_parse_address(pref: str, city: str, street: str) -> dict:
    parts = [p for p in [pref, city, street] if p and isinstance(p, str)]
    if not parts:
        return {}
    text = "".join(parts)
    try:
        return parse_address(text)
    except Exception:
        return {}


# ---------------------------------------------------------------------------
# Chunk parse — identical logic to parse_corpreg.py's parse_chunk
# ---------------------------------------------------------------------------

def parse_chunk(df: pd.DataFrame) -> pd.DataFrame:
    """Apply ja-entity-parser to a DataFrame chunk and return enriched copy."""
    name_cols: dict[str, list] = {k: [] for k in PARSED_NAME_COLS}
    addr_cols: dict[str, list] = {k: [] for k in PARSED_ADDR_COLS}

    for row in df.itertuples(index=False):
        cr = safe_parse_corporate(getattr(row, "name", None))
        name_cols["parsed_legal_form"].append(cr.get("legal_form"))
        name_cols["parsed_brand_name"].append(cr.get("brand_name"))
        name_cols["parsed_brand_kana"].append(cr.get("brand_kana"))
        name_cols["parsed_name_normalized"].append(cr.get("normalized"))

        ar = safe_parse_address(
            getattr(row, "prefecture_name", None),
            getattr(row, "city_name", None),
            getattr(row, "street_number", None),
        )
        addr_cols["parsed_state"].append(ar.get("state"))
        addr_cols["parsed_city"].append(ar.get("city"))
        addr_cols["parsed_suburb"].append(ar.get("suburb"))
        addr_cols["parsed_house_number"].append(ar.get("house_number"))
        addr_cols["parsed_house_number_raw"].append(ar.get("house_number_raw"))
        addr_cols["parsed_addr_normalized"].append(ar.get("normalized"))

    result = df.copy()
    for col, values in {**name_cols, **addr_cols}.items():
        result[col] = values
    return result


# ---------------------------------------------------------------------------
# Full-file parse: GCS raw → parse in chunks → GCS master
# ---------------------------------------------------------------------------

def parse_parquet_file(
    sc: storage.Client,
    src_bucket: str,
    src_blob: str,
    dst_bucket: str,
    dst_blob: str,
) -> int:
    """
    Download a raw parquet from GCS, run ja-entity-parser on every row
    in CHUNK_SIZE batches, write the enriched parquet back to GCS.

    Returns the total number of rows processed.
    """
    src_tmp = download_to_tmpfile(sc, src_bucket, src_blob)
    dst_tmp = Path(tempfile.mktemp(suffix=".parquet"))

    try:
        pf = pq.ParquetFile(str(src_tmp))
        total_rows = pf.metadata.num_rows
        logger.info(f"Total rows to parse: {total_rows:,}")

        # Build output schema = source schema + parsed_* string columns
        src_schema = pf.schema_arrow
        out_schema = pa.schema(list(src_schema) + PARSED_FIELDS)
        writer = pq.ParquetWriter(str(dst_tmp), out_schema)
        processed = 0

        for batch in pf.iter_batches(batch_size=CHUNK_SIZE):
            df_chunk = batch.to_pandas()
            df_parsed = parse_chunk(df_chunk)

            arrays = []
            for field in out_schema:
                col = df_parsed.get(field.name)
                if col is None:
                    col = df_chunk[field.name]
                try:
                    arrays.append(pa.array(col, type=field.type, from_pandas=True))
                except Exception:
                    arrays.append(
                        pa.array(
                            col.astype(str).where(col.notna(), other=None),
                            type=pa.string(),
                            from_pandas=True,
                        ).cast(field.type)
                        if field.type != pa.null()
                        else pa.array([None] * len(col), type=pa.string())
                    )
            table = pa.table(
                dict(zip([f.name for f in out_schema], arrays)), schema=out_schema
            )
            writer.write_table(table)

            processed += len(df_chunk)
            if processed % LOG_EVERY < CHUNK_SIZE or processed >= total_rows:
                logger.info(
                    f"  Progress: {processed:,}/{total_rows:,}"
                    f" ({processed / total_rows * 100:.1f}%)"
                )

        writer.close()
        logger.info(f"Parsing complete. {processed:,} rows written.")

        # Free source before upload
        src_tmp.unlink(missing_ok=True)
        gc.collect()

        upload_from_tmpfile(sc, dst_tmp, dst_bucket, dst_blob)
        logger.info(f"Done. Output: gs://{dst_bucket}/{dst_blob} ({processed:,} rows)")
        return processed

    finally:
        src_tmp.unlink(missing_ok=True)
        dst_tmp.unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# Closed-company helpers
# ---------------------------------------------------------------------------

def is_closed(df: pd.DataFrame) -> pd.Series:
    """
    Return a boolean mask for rows that represent closed companies.
    Definition: close_date is non-null OR process == '21'.
    """
    has_close_date = df["close_date"].notna() & (df["close_date"] != "")
    # process may be int or str depending on parquet schema
    is_process_21 = df["process"].astype(str) == "21"
    return has_close_date | is_process_21


def extract_closed_from_latest(
    sc: storage.Client, latest_blob: str = "corpreg_nta_latest.parquet"
) -> pd.DataFrame | None:
    """
    Download the previous latest parquet from GCS and return only
    closed-company rows. Returns None if the blob does not exist
    (first run).
    """
    if not blob_exists(sc, GCS_MASTER_BUCKET, latest_blob):
        logger.info(
            f"gs://{GCS_MASTER_BUCKET}/{latest_blob} does not exist "
            "(first run). Skipping closed-company extraction."
        )
        return None

    tmp = download_to_tmpfile(sc, GCS_MASTER_BUCKET, latest_blob)
    try:
        df = pd.read_parquet(str(tmp))
        closed = df[is_closed(df)].copy()
        logger.info(
            f"Extracted {len(closed):,} closed-company rows from previous latest."
        )
        return closed
    finally:
        tmp.unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# Merge logic (daily pipeline Step C)
# ---------------------------------------------------------------------------

def merge_diff_into_latest(
    sc: storage.Client,
    diff_blob: str = "corpreg_nta_diff.parquet",
    latest_blob: str = "corpreg_nta_latest.parquet",
) -> int:
    """
    Upsert diff records (latest=1 only) into the master latest parquet.

    Logic:
    - Load diff, keep only rows where latest == 1
    - Load current latest
    - For each corporate_number in diff, replace the row in latest
    - Write back to GCS

    Returns the number of upserted rows.
    """
    # --- Download diff ---
    diff_tmp = download_to_tmpfile(sc, GCS_MASTER_BUCKET, diff_blob)
    try:
        df_diff = pd.read_parquet(str(diff_tmp))
    finally:
        diff_tmp.unlink(missing_ok=True)

    # Keep only latest=1 rows from diff
    if "latest" in df_diff.columns:
        df_diff = df_diff[df_diff["latest"].astype(int) == 1].copy()
    upsert_count = len(df_diff)
    logger.info(f"Diff records to upsert (latest=1): {upsert_count:,}")

    if upsert_count == 0:
        logger.info("No records to upsert. Skipping merge.")
        return 0

    # --- Download current latest ---
    latest_tmp = download_to_tmpfile(sc, GCS_MASTER_BUCKET, latest_blob)
    try:
        df_latest = pd.read_parquet(str(latest_tmp))
    finally:
        latest_tmp.unlink(missing_ok=True)

    logger.info(f"Current latest rows: {len(df_latest):,}")

    # --- Upsert ---
    # Remove rows in latest that will be replaced by diff
    diff_corp_nums = set(df_diff["corporate_number"])
    df_latest = df_latest[~df_latest["corporate_number"].isin(diff_corp_nums)]

    # Append diff rows
    df_merged = pd.concat([df_latest, df_diff], ignore_index=True)
    logger.info(f"Merged latest rows: {len(df_merged):,}")

    # --- Write back ---
    merged_tmp = Path(tempfile.mktemp(suffix=".parquet"))
    try:
        df_merged.to_parquet(str(merged_tmp), engine="pyarrow", index=False)
        gc.collect()
        upload_from_tmpfile(sc, merged_tmp, GCS_MASTER_BUCKET, latest_blob)
    finally:
        merged_tmp.unlink(missing_ok=True)

    logger.info(f"Merge complete. Upserted {upsert_count:,} rows.")
    return upsert_count
