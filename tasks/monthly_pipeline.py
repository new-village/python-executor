"""
monthly_pipeline.py

Monthly batch pipeline for NTA corporate registry data.

Flow:
  1. Fetch all corporate records from NTA via jpcorpreg
  2. Upload raw parquet to GCS yata-raw/corpreg_nta_YYYYMM.parquet
  3. Parse/cleanse with ja-entity-parser → GCS yata-master (temp parsed file)
  4. Extract closed companies from previous yata-master/corpreg_nta_latest.parquet
  5. Append closed companies to the newly parsed data
  6. Write final result to yata-master/corpreg_nta_latest.parquet

Usage (Cloud Run Job):
  ENTRYPOINT ["python", "-m"]
  CMD ["tasks.monthly_pipeline"]

  Optional env: DATE=YYYYMM (defaults to current month)
"""

from __future__ import annotations

import gc
import logging
import os
import sys
import tempfile
from datetime import datetime
from pathlib import Path

import pandas as pd
import jpcorpreg

from tasks.pipeline_utils import (
    GCS_RAW_BUCKET,
    GCS_MASTER_BUCKET,
    gcs_client,
    upload_from_tmpfile,
    download_to_tmpfile,
    parse_parquet_file,
    extract_closed_from_latest,
    is_closed,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


def main() -> None:
    # Determine target month
    if len(sys.argv) > 1:
        yyyymm = sys.argv[1]
    else:
        yyyymm = os.environ.get("DATE", datetime.now().strftime("%Y%m"))

    raw_blob = f"corpreg_nta_{yyyymm}.parquet"
    parsed_blob = f"corpreg_nta_parsed_{yyyymm}.parquet"  # temp intermediate
    latest_blob = "corpreg_nta_latest.parquet"

    logger.info(f"=== Monthly Pipeline: {yyyymm} ===")

    sc = gcs_client()

    # ------------------------------------------------------------------
    # Step 1: Fetch all records from NTA
    # ------------------------------------------------------------------
    logger.info("Step 1: Fetching all corporate records from NTA ...")
    client = jpcorpreg.CorporateRegistryClient()
    df = client.fetch()

    if df is None or df.empty:
        logger.error("No data fetched from NTA. Aborting.")
        raise RuntimeError("NTA fetch returned empty data.")

    logger.info(f"Fetched {len(df):,} records.")

    # ------------------------------------------------------------------
    # Step 2: Save raw parquet to GCS via tmp file
    # ------------------------------------------------------------------
    logger.info(f"Step 2: Uploading raw data to gs://{GCS_RAW_BUCKET}/{raw_blob} ...")
    raw_tmp = Path(tempfile.mktemp(suffix=".parquet"))
    try:
        df.to_parquet(str(raw_tmp), engine="pyarrow", index=False)
        upload_from_tmpfile(sc, raw_tmp, GCS_RAW_BUCKET, raw_blob)
    finally:
        raw_tmp.unlink(missing_ok=True)

    # Free memory
    del df
    gc.collect()

    # ------------------------------------------------------------------
    # Step 3: Parse/cleanse → temp parsed file in GCS master
    # ------------------------------------------------------------------
    logger.info("Step 3: Parsing/cleansing with ja-entity-parser ...")
    parsed_rows = parse_parquet_file(
        sc,
        src_bucket=GCS_RAW_BUCKET,
        src_blob=raw_blob,
        dst_bucket=GCS_MASTER_BUCKET,
        dst_blob=parsed_blob,
    )
    logger.info(f"Parsed {parsed_rows:,} rows.")

    # ------------------------------------------------------------------
    # Step 4: Extract closed companies from previous latest
    # ------------------------------------------------------------------
    logger.info("Step 4: Extracting closed companies from previous latest ...")
    df_prev_closed = extract_closed_from_latest(sc, latest_blob)

    # ------------------------------------------------------------------
    # Step 5: Build final latest = parsed active + parsed closed + prev closed
    # ------------------------------------------------------------------
    logger.info("Step 5: Building final latest parquet ...")

    # Download the freshly parsed file
    parsed_tmp = download_to_tmpfile(sc, GCS_MASTER_BUCKET, parsed_blob)
    try:
        df_parsed = pd.read_parquet(str(parsed_tmp))
    finally:
        parsed_tmp.unlink(missing_ok=True)

    if df_prev_closed is not None and len(df_prev_closed) > 0:
        # Only append previously-closed companies that are NOT already
        # present in the new full-fetch (they might have reappeared or
        # been updated in the new NTA dump).
        existing_corp_nums = set(df_parsed["corporate_number"])
        new_closed = df_prev_closed[
            ~df_prev_closed["corporate_number"].isin(existing_corp_nums)
        ].copy()
        logger.info(
            f"Appending {len(new_closed):,} closed-company rows "
            f"from previous latest (after dedup)."
        )
        if len(new_closed) > 0:
            # Align columns — prev_closed may have same or different columns
            # Only keep columns present in both DataFrames; fill missing with None
            all_cols = list(df_parsed.columns)
            for col in all_cols:
                if col not in new_closed.columns:
                    new_closed[col] = None
            new_closed = new_closed[all_cols]
            df_parsed = pd.concat([df_parsed, new_closed], ignore_index=True)
    else:
        logger.info("No previous closed companies to append (first run or none found).")

    logger.info(f"Final latest row count: {len(df_parsed):,}")

    # ------------------------------------------------------------------
    # Step 6: Upload final latest to GCS
    # ------------------------------------------------------------------
    logger.info(f"Step 6: Uploading final latest to gs://{GCS_MASTER_BUCKET}/{latest_blob} ...")
    latest_tmp = Path(tempfile.mktemp(suffix=".parquet"))
    try:
        df_parsed.to_parquet(str(latest_tmp), engine="pyarrow", index=False)
        del df_parsed
        gc.collect()
        upload_from_tmpfile(sc, latest_tmp, GCS_MASTER_BUCKET, latest_blob)
    finally:
        latest_tmp.unlink(missing_ok=True)

    # ------------------------------------------------------------------
    # Cleanup: remove temp parsed file from GCS
    # ------------------------------------------------------------------
    logger.info(f"Cleaning up temp blob gs://{GCS_MASTER_BUCKET}/{parsed_blob} ...")
    try:
        bucket = sc.bucket(GCS_MASTER_BUCKET)
        bucket.blob(parsed_blob).delete()
        logger.info("  Temp blob deleted.")
    except Exception as e:
        logger.warning(f"  Could not delete temp blob: {e}")

    logger.info("=== Monthly Pipeline complete. ===")


if __name__ == "__main__":
    main()
