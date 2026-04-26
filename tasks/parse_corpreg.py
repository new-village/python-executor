"""
parse_corpreg.py

Parse corporate names and addresses in the NTA corporate registry Parquet file
using ja-entity-parser, and write the enriched result to GCS yata-master.

Input:  gs://yata-raw/corpreg_nta_YYYYMM.parquet
Output: gs://yata-master/corpreg_parsed_YYYYMM.parquet

Usage (Cloud Run Job):
  TASK_MODULE=tasks.parse_corpreg  (+ optional DATE env var, default: current month)

Usage (local):
  python -m tasks.parse_corpreg [YYYYMM]

Output columns (added on top of original):
  parsed_legal_form        - 法人種別 (e.g. 株式会社)
  parsed_brand_name        - ブランド名 (法人種別除く)
  parsed_brand_kana        - ブランド名カナ
  parsed_name_normalized   - 正規化済み法人名
  parsed_prefecture        - 都道府県 (from address parser)
  parsed_city              - 市区町村
  parsed_town              - 町名
  parsed_block             - 番地 (正規化済み: 半角数字+ハイフン)
  parsed_block_raw         - 番地 原文
  parsed_addr_normalized   - 正規化済み住所
"""

from __future__ import annotations

import io
import logging
import os
import sys
from datetime import datetime

import pandas as pd
from google.cloud import storage

# ja-entity-parser
from ja_entityparser import parse_corporate, parse_address

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
SRC_BUCKET = "yata-raw"
DST_BUCKET = "yata-master"
PROJECT_ID = "yata-intelligence"

BATCH_SIZE = 10_000  # rows per log checkpoint

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def gcs_client() -> storage.Client:
    return storage.Client(project=PROJECT_ID)


def load_parquet_from_gcs(sc: storage.Client, bucket_name: str, blob_name: str) -> pd.DataFrame:
    logger.info(f"Downloading gs://{bucket_name}/{blob_name} ...")
    bucket = sc.bucket(bucket_name)
    blob = bucket.blob(blob_name)
    data = blob.download_as_bytes()
    df = pd.read_parquet(io.BytesIO(data), engine="pyarrow")
    logger.info(f"  Loaded {len(df):,} rows, {len(df.columns)} columns.")
    return df


def upload_parquet_to_gcs(sc: storage.Client, df: pd.DataFrame, bucket_name: str, blob_name: str) -> None:
    logger.info(f"Uploading {len(df):,} rows → gs://{bucket_name}/{blob_name} ...")
    buf = io.BytesIO()
    df.to_parquet(buf, engine="pyarrow", index=False)
    buf.seek(0)
    bucket = sc.bucket(bucket_name)
    blob = bucket.blob(blob_name)
    blob.upload_from_file(buf, content_type="application/octet-stream")
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
# Batch parse
# ---------------------------------------------------------------------------

def parse_dataframe(df: pd.DataFrame) -> pd.DataFrame:
    total = len(df)
    logger.info(f"Parsing {total:,} rows ...")

    # Pre-allocate result columns
    name_cols = {
        "parsed_legal_form": [],
        "parsed_brand_name": [],
        "parsed_brand_kana": [],
        "parsed_name_normalized": [],
    }
    addr_cols = {
        "parsed_prefecture": [],
        "parsed_city": [],
        "parsed_town": [],
        "parsed_block": [],
        "parsed_block_raw": [],
        "parsed_addr_normalized": [],
    }

    for i, row in enumerate(df.itertuples(index=False), start=1):
        # Corporate name
        cr = safe_parse_corporate(getattr(row, "name", None))
        name_cols["parsed_legal_form"].append(cr.get("legal_form"))
        name_cols["parsed_brand_name"].append(cr.get("brand_name"))
        name_cols["parsed_brand_kana"].append(cr.get("brand_kana"))
        name_cols["parsed_name_normalized"].append(cr.get("normalized"))

        # Address
        ar = safe_parse_address(
            getattr(row, "prefecture_name", None),
            getattr(row, "city_name", None),
            getattr(row, "street_number", None),
        )
        addr_cols["parsed_prefecture"].append(ar.get("prefecture"))
        addr_cols["parsed_city"].append(ar.get("city"))
        addr_cols["parsed_town"].append(ar.get("town"))
        addr_cols["parsed_block"].append(ar.get("block"))
        addr_cols["parsed_block_raw"].append(ar.get("block_raw"))
        addr_cols["parsed_addr_normalized"].append(ar.get("normalized"))

        if i % BATCH_SIZE == 0 or i == total:
            logger.info(f"  Progress: {i:,}/{total:,} ({i/total*100:.1f}%)")

    result = df.copy()
    for col, values in {**name_cols, **addr_cols}.items():
        result[col] = values

    logger.info("Parsing complete.")
    return result


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    # Determine YYYYMM — from CLI arg, DATE env var, or current month
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

    df = load_parquet_from_gcs(sc, SRC_BUCKET, src_blob)
    parsed = parse_dataframe(df)
    upload_parquet_to_gcs(sc, parsed, DST_BUCKET, dst_blob)

    logger.info(f"Done. Output: gs://{DST_BUCKET}/{dst_blob} ({len(parsed):,} rows)")


if __name__ == "__main__":
    main()
