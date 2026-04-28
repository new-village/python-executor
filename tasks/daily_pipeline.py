"""
daily_pipeline.py

Daily batch pipeline for NTA corporate registry diff data.

Flow:
  Step A: Fetch diff records from NTA → GCS yata-raw/corpreg_nta_YYYYMMDD.parquet
  Step B: Parse/cleanse with ja-entity-parser → GCS yata-master/corpreg_nta_diff.parquet
  Step C: Merge diff into yata-master/corpreg_nta_latest.parquet (upsert by corporate_number)

Usage (Cloud Run Job):
  ENTRYPOINT ["python", "-m"]
  CMD ["tasks.daily_pipeline"]

  Optional env: DATE=YYYYMMDD (defaults to today)

Scheduling: Cloud Scheduler, weekdays 17:00 JST.

Note: If NTA has no diff data for the target date (holidays, early morning),
the pipeline exits successfully with code 0 — this is normal.
"""

from __future__ import annotations

import gc
import logging
import os
import sys
import tempfile
from datetime import datetime
from pathlib import Path

import jpcorpreg

from tasks.pipeline_utils import (
    GCS_RAW_BUCKET,
    GCS_MASTER_BUCKET,
    gcs_client,
    upload_from_tmpfile,
    parse_parquet_file,
    merge_diff_into_latest,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


def main() -> None:
    # Determine target date
    if len(sys.argv) > 1:
        target_date = sys.argv[1]
    else:
        target_date = os.environ.get("DATE", datetime.now().strftime("%Y%m%d"))

    raw_blob = f"corpreg_nta_{target_date}.parquet"
    diff_blob = "corpreg_nta_diff.parquet"
    latest_blob = "corpreg_nta_latest.parquet"

    logger.info(f"=== Daily Pipeline: {target_date} ===")

    sc = gcs_client()

    # ------------------------------------------------------------------
    # Step A: Fetch diff from NTA
    # ------------------------------------------------------------------
    logger.info(f"Step A: Fetching diff data for {target_date} ...")
    client = jpcorpreg.CorporateRegistryClient()

    try:
        df = client.fetch_diff(date=target_date)
    except ValueError as ve:
        if "No sabun data found" in str(ve):
            logger.info(
                f"No diff data published for {target_date} "
                "(normal for holidays or early morning). Exiting normally."
            )
            return  # exit 0
        raise

    if df is None or df.empty:
        logger.info(f"No diff data for {target_date}. Exiting normally.")
        return  # exit 0

    record_count = len(df)
    logger.info(f"Fetched {record_count:,} diff records.")

    # ------------------------------------------------------------------
    # Step A (cont): Upload raw diff to GCS
    # ------------------------------------------------------------------
    logger.info(f"Uploading raw diff to gs://{GCS_RAW_BUCKET}/{raw_blob} ...")
    raw_tmp = Path(tempfile.mktemp(suffix=".parquet"))
    try:
        df.to_parquet(str(raw_tmp), engine="pyarrow", index=False)
        upload_from_tmpfile(sc, raw_tmp, GCS_RAW_BUCKET, raw_blob)
    finally:
        raw_tmp.unlink(missing_ok=True)

    del df
    gc.collect()

    # ------------------------------------------------------------------
    # Step B: Parse/cleanse → yata-master/corpreg_nta_diff.parquet
    # ------------------------------------------------------------------
    logger.info("Step B: Parsing/cleansing diff data ...")
    parsed_rows = parse_parquet_file(
        sc,
        src_bucket=GCS_RAW_BUCKET,
        src_blob=raw_blob,
        dst_bucket=GCS_MASTER_BUCKET,
        dst_blob=diff_blob,
    )
    logger.info(f"Parsed {parsed_rows:,} diff rows → gs://{GCS_MASTER_BUCKET}/{diff_blob}")

    # ------------------------------------------------------------------
    # Step C: Merge diff into latest
    # ------------------------------------------------------------------
    logger.info("Step C: Merging diff into latest ...")
    upserted = merge_diff_into_latest(sc, diff_blob=diff_blob, latest_blob=latest_blob)
    logger.info(f"Upserted {upserted:,} rows into latest.")

    logger.info("=== Daily Pipeline complete. ===")


if __name__ == "__main__":
    main()
