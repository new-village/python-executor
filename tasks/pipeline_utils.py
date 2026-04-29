"""
pipeline_utils.py

Shared utilities for the corpreg NTA monthly/daily pipelines:
- GCS helpers (download, upload, exists)
- Parse helpers (safe wrappers around ja-entity-parser)
- Chunk parse function
- Closed-company extraction and append
- Merge logic (upsert diff into latest)

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

    Definition (from NTA spec):
    - process == '21' (閉鎖等登記) OR process == '22' (削除)
    - OR close_date is non-null and non-empty

    Note: The caller is responsible for filtering latest=='1' before
    calling this function if needed.
    """
    has_close_date = df["close_date"].notna() & (df["close_date"] != "")
    process_str = df["process"].astype(str)
    is_process_closed = (process_str == "21") | (process_str == "22")
    return has_close_date | is_process_closed


def extract_and_append_closed(
    sc: storage.Client,
    src_blob: str = "corpreg_nta_diff.parquet",
    closed_blob: str = "corpreg_nta_closed.parquet",
) -> int:
    """
    Extract closed-company rows from the diff parquet and append them
    to the cumulative closed parquet in GCS.

    Filters: latest=='1' AND is_closed().
    If closed_blob already exists, reads it and concatenates.
    Deduplicates by corporate_number (keeps the latest entry from diff).

    Returns the number of newly appended closed rows.
    """
    # Download diff
    diff_tmp = download_to_tmpfile(sc, GCS_MASTER_BUCKET, src_blob)
    try:
        df_diff = pd.read_parquet(str(diff_tmp))
    finally:
        diff_tmp.unlink(missing_ok=True)

    # Filter: latest=='1' only
    if "latest" in df_diff.columns:
        df_diff = df_diff[df_diff["latest"].astype(str) == "1"].copy()

    # Apply closed filter
    mask = is_closed(df_diff)
    df_new_closed = df_diff[mask].copy()
    new_count = len(df_new_closed)
    logger.info(f"Extracted {new_count:,} closed rows from diff.")

    if new_count == 0:
        logger.info("No closed rows to append. Skipping.")
        return 0

    # Load existing closed parquet if it exists
    if blob_exists(sc, GCS_MASTER_BUCKET, closed_blob):
        existing_tmp = download_to_tmpfile(sc, GCS_MASTER_BUCKET, closed_blob)
        try:
            df_existing = pd.read_parquet(str(existing_tmp))
        finally:
            existing_tmp.unlink(missing_ok=True)
        logger.info(f"Existing closed rows: {len(df_existing):,}")

        # Remove rows that will be replaced by newer diff entries
        new_corp_nums = set(df_new_closed["corporate_number"])
        df_existing = df_existing[
            ~df_existing["corporate_number"].isin(new_corp_nums)
        ]

        # Align columns
        all_cols = list(df_new_closed.columns)
        for col in all_cols:
            if col not in df_existing.columns:
                df_existing[col] = None
        df_existing = df_existing[all_cols]

        df_closed = pd.concat([df_existing, df_new_closed], ignore_index=True)
    else:
        logger.info("No existing closed parquet (first run). Creating new.")
        df_closed = df_new_closed

    logger.info(f"Total closed rows after append: {len(df_closed):,}")

    # Write back
    out_tmp = Path(tempfile.mktemp(suffix=".parquet"))
    try:
        df_closed.to_parquet(str(out_tmp), engine="pyarrow", index=False)
        upload_from_tmpfile(sc, out_tmp, GCS_MASTER_BUCKET, closed_blob)
    finally:
        out_tmp.unlink(missing_ok=True)

    return new_count


def append_closed_to_latest(
    sc: storage.Client,
    closed_blob: str = "corpreg_nta_closed.parquet",
    active_blob: str = "corpreg_nta_active.parquet",
    latest_blob: str = "corpreg_nta_latest.parquet",
) -> int:
    """
    Combine active + closed parquets to produce the latest parquet.

    - Reads active_blob (required — must exist)
    - If closed_blob exists, appends closed rows not already in active
    - Writes the result to latest_blob (overwrite)

    Returns the total row count of the latest parquet.
    """
    # Load active (required)
    active_tmp = download_to_tmpfile(sc, GCS_MASTER_BUCKET, active_blob)
    try:
        df_active = pd.read_parquet(str(active_tmp))
    finally:
        active_tmp.unlink(missing_ok=True)
    logger.info(f"Active rows: {len(df_active):,}")

    # Load closed (optional)
    if blob_exists(sc, GCS_MASTER_BUCKET, closed_blob):
        closed_tmp = download_to_tmpfile(sc, GCS_MASTER_BUCKET, closed_blob)
        try:
            df_closed = pd.read_parquet(str(closed_tmp))
        finally:
            closed_tmp.unlink(missing_ok=True)
        logger.info(f"Closed rows: {len(df_closed):,}")

        # Only append closed rows whose corporate_number is NOT in active
        active_corp_nums = set(df_active["corporate_number"])
        df_closed_new = df_closed[
            ~df_closed["corporate_number"].isin(active_corp_nums)
        ].copy()
        logger.info(
            f"Closed rows to append (after dedup vs active): {len(df_closed_new):,}"
        )

        if len(df_closed_new) > 0:
            # Align columns
            all_cols = list(df_active.columns)
            for col in all_cols:
                if col not in df_closed_new.columns:
                    df_closed_new[col] = None
            df_closed_new = df_closed_new[all_cols]
            df_latest = pd.concat([df_active, df_closed_new], ignore_index=True)
        else:
            df_latest = df_active
    else:
        logger.info(
            "No closed parquet found. Using active as latest directly."
        )
        df_latest = df_active

    total = len(df_latest)
    logger.info(f"Latest row count: {total:,}")

    # Write latest
    out_tmp = Path(tempfile.mktemp(suffix=".parquet"))
    try:
        df_latest.to_parquet(str(out_tmp), engine="pyarrow", index=False)
        del df_latest
        gc.collect()
        upload_from_tmpfile(sc, out_tmp, GCS_MASTER_BUCKET, latest_blob)
    finally:
        out_tmp.unlink(missing_ok=True)

    return total


# ---------------------------------------------------------------------------
# Merge logic (daily pipeline: upsert diff into latest)
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
    - Load current latest (if it doesn't exist, use diff as initial latest)
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

    # --- Download current latest (or handle first run) ---
    if not blob_exists(sc, GCS_MASTER_BUCKET, latest_blob):
        logger.info(
            f"gs://{GCS_MASTER_BUCKET}/{latest_blob} does not exist "
            "(first run). Using diff as initial latest."
        )
        merged_tmp = Path(tempfile.mktemp(suffix=".parquet"))
        try:
            df_diff.to_parquet(str(merged_tmp), engine="pyarrow", index=False)
            upload_from_tmpfile(sc, merged_tmp, GCS_MASTER_BUCKET, latest_blob)
        finally:
            merged_tmp.unlink(missing_ok=True)
        logger.info(f"Initial latest created with {upsert_count:,} rows.")
        return upsert_count

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
