"""
parse_corpreg.py

Parse corporate names and addresses in the NTA corporate registry Parquet file
using ja-entity-parser, and write the enriched result to GCS yata-master.

Input:  gs://yata-raw/corpreg_nta_YYYYMM.parquet
Output: gs://yata-master/corpreg_parsed_YYYYMM.parquet

Processes in chunks to avoid OOM on large files (5M+ rows).

Usage (Cloud Run Job):
  args: ["tasks.parse_corpreg", "202603"]

Usage (local):
  python -m tasks.parse_corpreg [YYYYMM]

Output columns (added on top of original):
  parsed_legal_form        - 法人種別 (e.g. 株式会社)
  parsed_brand_name        - ブランド名 (法人種別除く)
  parsed_brand_kana        - ブランド名カナ
  parsed_name_normalized   - 正規化済み法人名
  parsed_state             - 都道府県 (field: state)
  parsed_city              - 市区町村 (field: city)
  parsed_suburb            - 町名 (field: suburb)
  parsed_house_number      - 番地 (正規化済み; field: house_number)
  parsed_house_number_raw  - 番地 原文 (field: house_number_raw)
  parsed_addr_normalized   - 正規化済み住所
"""

from __future__ import annotations

import io
import logging
import os
import sys
import tempfile
from datetime import datetime
from pathlib import Path

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
from google.cloud import storage

# ja-entity-parser
from ja_entityparser import parse_corporate, parse_address

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
SRC_BUCKET = "yata-raw"
DST_BUCKET = "yata-master"
PROJECT_ID = "yata-intelligence"

CHUNK_SIZE = 50_000   # rows per processing chunk
LOG_EVERY = 200_000   # rows between progress log lines

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# GCS helpers
# ---------------------------------------------------------------------------

def gcs_client() -> storage.Client:
    return storage.Client(project=PROJECT_ID)


def download_to_tmpfile(sc: storage.Client, bucket_name: str, blob_name: str) -> Path:
    """Stream-download GCS blob to a local temp file and return its Path."""
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


def upload_from_tmpfile(sc: storage.Client, local_path: Path, bucket_name: str, blob_name: str) -> None:
    size_mb = local_path.stat().st_size / 1024 / 1024
    logger.info(f"Uploading {local_path} ({size_mb:.1f} MB) → gs://{bucket_name}/{blob_name} ...")
    bucket = sc.bucket(bucket_name)
    blob = bucket.blob(blob_name)
    # upload_from_filename uses resumable upload internally and does not
    # load the entire file into memory at once.
    blob.upload_from_filename(
        str(local_path),
        content_type="application/octet-stream",
    )
    logger.info("  Upload complete.")


# ---------------------------------------------------------------------------
# Parsing helpers (single-row, error-safe)
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
# Chunk parse
# ---------------------------------------------------------------------------

def parse_chunk(df: pd.DataFrame) -> pd.DataFrame:
    name_cols: dict[str, list] = {k: [] for k in [
        "parsed_legal_form", "parsed_brand_name", "parsed_brand_kana", "parsed_name_normalized"
    ]}
    addr_cols: dict[str, list] = {k: [] for k in [
        "parsed_state", "parsed_city", "parsed_suburb",
        "parsed_house_number", "parsed_house_number_raw", "parsed_addr_normalized"
    ]}

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
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    if len(sys.argv) > 1:
        yyyymm = sys.argv[1]
    else:
        yyyymm = os.environ.get("DATE", datetime.now().strftime("%Y%m"))

    src_blob = f"corpreg_nta_{yyyymm}.parquet"
    dst_blob = f"corpreg_parsed_{yyyymm}.parquet"

    logger.info(f"=== parse_corpreg: {yyyymm} ===")
    logger.info(f"  Source: gs://{SRC_BUCKET}/{src_blob}")
    logger.info(f"  Destination: gs://{DST_BUCKET}/{dst_blob}")

    sc = gcs_client()

    # Download source to local temp file
    src_tmp = download_to_tmpfile(sc, SRC_BUCKET, src_blob)

    # Prepare output temp file (ParquetWriter for streaming write)
    dst_tmp = Path(tempfile.mktemp(suffix=".parquet"))

    try:
        pf = pq.ParquetFile(str(src_tmp))
        total_rows = pf.metadata.num_rows
        logger.info(f"Total rows: {total_rows:,}")

        # Build output schema = source schema + parsed_* string columns
        src_schema = pf.schema_arrow
        parsed_fields = [
            pa.field("parsed_legal_form", pa.string()),
            pa.field("parsed_brand_name", pa.string()),
            pa.field("parsed_brand_kana", pa.string()),
            pa.field("parsed_name_normalized", pa.string()),
            pa.field("parsed_state", pa.string()),
            pa.field("parsed_city", pa.string()),
            pa.field("parsed_suburb", pa.string()),
            pa.field("parsed_house_number", pa.string()),
            pa.field("parsed_house_number_raw", pa.string()),
            pa.field("parsed_addr_normalized", pa.string()),
        ]
        out_schema = pa.schema(list(src_schema) + parsed_fields)
        writer = pq.ParquetWriter(str(dst_tmp), out_schema)
        processed = 0

        for batch in pf.iter_batches(batch_size=CHUNK_SIZE):
            df_chunk = batch.to_pandas()
            df_parsed = parse_chunk(df_chunk)

            # Build arrow table using the fixed output schema
            arrays = []
            for field in out_schema:
                col = df_parsed[field.name] if field.name in df_parsed.columns else df_chunk[field.name]
                try:
                    arrays.append(pa.array(col, type=field.type, from_pandas=True))
                except Exception:
                    # Fallback: cast via string for problematic columns
                    arrays.append(pa.array(col.astype(str).where(col.notna(), other=None), type=pa.string(), from_pandas=True).cast(field.type) if field.type != pa.null() else pa.array([None]*len(col), type=pa.string()))
            table = pa.table(dict(zip([f.name for f in out_schema], arrays)), schema=out_schema)
            writer.write_table(table)

            processed += len(df_chunk)
            if processed % LOG_EVERY < CHUNK_SIZE or processed >= total_rows:
                logger.info(f"  Progress: {processed:,}/{total_rows:,} ({processed/total_rows*100:.1f}%)")

        if writer:
            writer.close()

        logger.info(f"Parsing complete. {processed:,} rows written.")

        # Free up memory before upload (source parquet no longer needed)
        src_tmp.unlink(missing_ok=True)
        import gc; gc.collect()

        # Upload result
        upload_from_tmpfile(sc, dst_tmp, DST_BUCKET, dst_blob)
        logger.info(f"Done. Output: gs://{DST_BUCKET}/{dst_blob} ({processed:,} rows)")

    finally:
        src_tmp.unlink(missing_ok=True)
        dst_tmp.unlink(missing_ok=True)


if __name__ == "__main__":
    main()
