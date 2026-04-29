"""
monthly_pipeline.py

Monthly batch pipeline for NTA corporate registry data (To-Be v5).

Flow:
  Step 1: Fetch all corporate records from NTA → GCS yata-raw/corpreg_nta_YYYYMM.parquet
  Step 2: Parse/cleanse with ja-entity-parser → GCS yata-master/corpreg_nta_active.parquet (overwrite)
  Step 3: Combine closed + active → GCS yata-master/corpreg_nta_latest.parquet (overwrite)
          If corpreg_nta_closed.parquet does not exist, active is used as latest directly.

Usage (Cloud Run Job):
  ENTRYPOINT ["python", "-m"]
  CMD ["tasks.monthly_pipeline"]

  Optional arg: YYYYMM (defaults to current month)
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
    append_closed_to_latest,
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
    active_blob = "corpreg_nta_active.parquet"
    closed_blob = "corpreg_nta_closed.parquet"
    latest_blob = "corpreg_nta_latest.parquet"

    logger.info(f"=== Monthly Pipeline (v5): {yyyymm} ===")

    sc = gcs_client()

    # ------------------------------------------------------------------
    # Step 1: Fetch all records from NTA → yata-raw
    # ------------------------------------------------------------------
    logger.info("Step 1: Fetching all corporate records from NTA ...")
    client = jpcorpreg.CorporateRegistryClient()
    df = client.fetch()

    if df is None or df.empty:
        logger.error("No data fetched from NTA. Aborting.")
        raise RuntimeError("NTA fetch returned empty data.")

    logger.info(f"Fetched {len(df):,} records.")

    # Upload raw parquet to GCS
    logger.info(f"Uploading raw data to gs://{GCS_RAW_BUCKET}/{raw_blob} ...")
    raw_tmp = Path(tempfile.mktemp(suffix=".parquet"))
    try:
        df.to_parquet(str(raw_tmp), engine="pyarrow", index=False)
        upload_from_tmpfile(sc, raw_tmp, GCS_RAW_BUCKET, raw_blob)
    finally:
        raw_tmp.unlink(missing_ok=True)

    del df
    gc.collect()

    # ------------------------------------------------------------------
    # Step 2: Parse/cleanse → yata-master/corpreg_nta_active.parquet
    # ------------------------------------------------------------------
    logger.info("Step 2: Parsing/cleansing with ja-entity-parser ...")
    parsed_rows = parse_parquet_file(
        sc,
        src_bucket=GCS_RAW_BUCKET,
        src_blob=raw_blob,
        dst_bucket=GCS_MASTER_BUCKET,
        dst_blob=active_blob,
    )
    logger.info(f"Parsed {parsed_rows:,} rows → gs://{GCS_MASTER_BUCKET}/{active_blob}")

    # ------------------------------------------------------------------
    # Step 3: Combine closed + active → latest
    # ------------------------------------------------------------------
    logger.info("Step 3: Building latest from active + closed ...")
    total = append_closed_to_latest(
        sc,
        closed_blob=closed_blob,
        active_blob=active_blob,
        latest_blob=latest_blob,
    )
    logger.info(f"Latest parquet: {total:,} rows → gs://{GCS_MASTER_BUCKET}/{latest_blob}")

    logger.info("=== Monthly Pipeline (v5) complete. ===")


if __name__ == "__main__":
    main()
