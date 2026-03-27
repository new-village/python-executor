"""
load_corpreg_to_bq.py

Load NTA corporate registry Parquet files from GCS into BigQuery.

Tables:
  corpreg.latest  - Current state (one row per corporate_number, latest=1 only)
  corpreg.history - Full change history (all records, append-only)

Usage:
  # Initial load (full snapshot from monthly file):
  python -m tasks.load_corpreg_to_bq --mode init

  # Daily incremental load (diff file for a specific date):
  python -m tasks.load_corpreg_to_bq --mode diff --date YYYYMMDD
  python -m tasks.load_corpreg_to_bq --mode diff   # defaults to today
"""

import argparse
import logging
import sys
from datetime import datetime

from google.cloud import bigquery, storage

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
GCS_BUCKET = "yata-raw"
PROJECT_ID = "yata-intelligence"
DATASET    = "corpreg"
TABLE_LATEST  = f"{PROJECT_ID}.{DATASET}.latest"
TABLE_HISTORY = f"{PROJECT_ID}.{DATASET}.history"
TABLE_STAGING = f"{PROJECT_ID}.{DATASET}.staging"

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def ensure_dataset(client: bigquery.Client) -> None:
    """Create dataset if it doesn't exist (no-op if already exists)."""
    from google.api_core.exceptions import Conflict
    ds_ref = bigquery.DatasetReference(PROJECT_ID, DATASET)
    ds = bigquery.Dataset(ds_ref)
    ds.location = "asia-northeast1"
    try:
        client.create_dataset(ds)
        logger.info(f"Created dataset {DATASET}.")
    except Conflict:
        logger.info(f"Dataset {DATASET} already exists.")
    except Exception as e:
        # Permission denied on create — assume dataset was pre-created externally
        logger.warning(f"Could not create dataset (may already exist): {e}")
        logger.info("Proceeding assuming dataset exists.")


def gcs_file_exists(bucket_name: str, blob_name: str) -> bool:
    sc = storage.Client()
    bucket = sc.bucket(bucket_name)
    return bucket.blob(blob_name).exists()


def load_parquet_to_table(
    client: bigquery.Client,
    gcs_uri: str,
    destination: str,
    write_disposition: str,
) -> None:
    job_config = bigquery.LoadJobConfig(
        source_format=bigquery.SourceFormat.PARQUET,
        write_disposition=write_disposition,
        autodetect=True,
    )
    logger.info(f"Loading {gcs_uri} → {destination} ({write_disposition}) ...")
    job = client.load_table_from_uri(gcs_uri, destination, job_config=job_config)
    job.result()  # wait
    table = client.get_table(destination)
    logger.info(f"  Done. {table.num_rows:,} rows in {destination}.")


def run_query(client: bigquery.Client, sql: str, desc: str = "") -> None:
    logger.info(f"Running query: {desc or sql[:80]}")
    job = client.query(sql)
    job.result()
    logger.info("  Query completed.")


# ---------------------------------------------------------------------------
# Mode: init
# ---------------------------------------------------------------------------

def init_load(client: bigquery.Client, yyyymm: str) -> None:
    """
    Initial load from monthly full snapshot.
    - corpreg.latest  : WRITE_TRUNCATE (replace all)
    - corpreg.history : WRITE_APPEND   (seed historical baseline)
    """
    blob_name = f"corpreg_nta_{yyyymm}.parquet"
    gcs_uri   = f"gs://{GCS_BUCKET}/{blob_name}"

    if not gcs_file_exists(GCS_BUCKET, blob_name):
        logger.error(f"File not found in GCS: {blob_name}")
        sys.exit(1)

    ensure_dataset(client)

    # Load into corpreg.latest (replace)
    load_parquet_to_table(
        client, gcs_uri, TABLE_LATEST,
        bigquery.WriteDisposition.WRITE_TRUNCATE,
    )

    # Add management column _loaded_at (one statement at a time)
    run_query(client,
        f"ALTER TABLE `{TABLE_LATEST}` ADD COLUMN IF NOT EXISTS _loaded_at TIMESTAMP",
        "_loaded_at column (latest)")
    run_query(client,
        f"UPDATE `{TABLE_LATEST}` SET _loaded_at = CURRENT_TIMESTAMP() WHERE _loaded_at IS NULL",
        "backfill _loaded_at (latest)")

    # Load into corpreg.history (append as baseline snapshot)
    load_parquet_to_table(
        client, gcs_uri, TABLE_HISTORY,
        bigquery.WriteDisposition.WRITE_APPEND,
    )
    run_query(client,
        f"ALTER TABLE `{TABLE_HISTORY}` ADD COLUMN IF NOT EXISTS source_file STRING",
        "source_file column (history)")
    run_query(client,
        f"ALTER TABLE `{TABLE_HISTORY}` ADD COLUMN IF NOT EXISTS _loaded_at TIMESTAMP",
        "_loaded_at column (history)")
    run_query(client,
        f"UPDATE `{TABLE_HISTORY}` SET source_file = '{blob_name}', _loaded_at = CURRENT_TIMESTAMP() WHERE source_file IS NULL",
        "backfill metadata (history)")

    logger.info("Initial load complete.")


# ---------------------------------------------------------------------------
# Mode: diff
# ---------------------------------------------------------------------------

MERGE_SQL = """
MERGE `{latest}` AS T
USING (SELECT * FROM `{staging}` WHERE latest = '1') AS S
ON T.corporate_number = S.corporate_number
WHEN MATCHED THEN UPDATE SET
  sequence_number              = S.sequence_number,
  process                      = S.process,
  correct                      = S.correct,
  update_date                  = S.update_date,
  change_date                  = S.change_date,
  name                         = S.name,
  name_image_id                = S.name_image_id,
  kind                         = S.kind,
  prefecture_name              = S.prefecture_name,
  city_name                    = S.city_name,
  street_number                = S.street_number,
  address_image_id             = S.address_image_id,
  prefecture_code              = S.prefecture_code,
  city_code                    = S.city_code,
  post_code                    = S.post_code,
  address_outside              = S.address_outside,
  address_outside_image_id     = S.address_outside_image_id,
  close_date                   = S.close_date,
  close_cause                  = S.close_cause,
  successor_corporate_number   = S.successor_corporate_number,
  change_cause                 = S.change_cause,
  assignment_date              = S.assignment_date,
  latest                       = S.latest,
  en_name                      = S.en_name,
  en_prefecture_name           = S.en_prefecture_name,
  en_city_name                 = S.en_city_name,
  en_address_outside           = S.en_address_outside,
  furigana                     = S.furigana,
  hihyoji                      = S.hihyoji,
  _loaded_at                   = CURRENT_TIMESTAMP()
WHEN NOT MATCHED THEN
  INSERT (
    corporate_number, sequence_number, process, correct, update_date, change_date,
    name, name_image_id, kind, prefecture_name, city_name, street_number,
    address_image_id, prefecture_code, city_code, post_code,
    address_outside, address_outside_image_id, close_date, close_cause,
    successor_corporate_number, change_cause, assignment_date, latest,
    en_name, en_prefecture_name, en_city_name, en_address_outside,
    furigana, hihyoji, _loaded_at
  )
  VALUES (
    S.corporate_number, S.sequence_number, S.process, S.correct, S.update_date, S.change_date,
    S.name, S.name_image_id, S.kind, S.prefecture_name, S.city_name, S.street_number,
    S.address_image_id, S.prefecture_code, S.city_code, S.post_code,
    S.address_outside, S.address_outside_image_id, S.close_date, S.close_cause,
    S.successor_corporate_number, S.change_cause, S.assignment_date, S.latest,
    S.en_name, S.en_prefecture_name, S.en_city_name, S.en_address_outside,
    S.furigana, S.hihyoji, CURRENT_TIMESTAMP()
  )
"""


def diff_load(client: bigquery.Client, yyyymmdd: str) -> None:
    """
    Daily diff load:
    1. Load diff Parquet → staging (replace)
    2. Filter latest=1 from staging → MERGE into corpreg.latest
    3. Append all records from staging → corpreg.history
    4. Drop staging
    """
    blob_name = f"corpreg_nta_{yyyymmdd}.parquet"
    gcs_uri   = f"gs://{GCS_BUCKET}/{blob_name}"

    if not gcs_file_exists(GCS_BUCKET, blob_name):
        logger.warning(f"Diff file not found in GCS: {blob_name}. Skipping.")
        sys.exit(0)

    ensure_dataset(client)

    # Step 1: Load full diff → staging (WRITE_TRUNCATE)
    load_parquet_to_table(
        client, gcs_uri, TABLE_STAGING,
        bigquery.WriteDisposition.WRITE_TRUNCATE,
    )
    run_query(client,
        f"ALTER TABLE `{TABLE_STAGING}` ADD COLUMN IF NOT EXISTS source_file STRING",
        "source_file column (staging)")
    run_query(client,
        f"ALTER TABLE `{TABLE_STAGING}` ADD COLUMN IF NOT EXISTS _loaded_at TIMESTAMP",
        "_loaded_at column (staging)")
    run_query(client,
        f"UPDATE `{TABLE_STAGING}` SET source_file = '{blob_name}', _loaded_at = CURRENT_TIMESTAMP() WHERE source_file IS NULL",
        "backfill metadata (staging)")

    # Step 2: MERGE latest=1 records → corpreg.latest
    merge_sql = MERGE_SQL.format(latest=TABLE_LATEST, staging=TABLE_STAGING)
    run_query(client, merge_sql, "MERGE into corpreg.latest")

    # Step 3: Append ALL records → corpreg.history
    run_query(client, f"""
        INSERT INTO `{TABLE_HISTORY}`
        SELECT * FROM `{TABLE_STAGING}`
    """, "INSERT into corpreg.history")

    # Step 4: Drop staging
    run_query(client, f"DROP TABLE IF EXISTS `{TABLE_STAGING}`", "drop staging")

    logger.info(f"Diff load complete for {yyyymmdd}.")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="Load NTA corpreg Parquet → BigQuery")
    parser.add_argument(
        "--mode",
        choices=["init", "diff"],
        required=True,
        help="'init' for initial full load, 'diff' for daily incremental",
    )
    parser.add_argument(
        "--date",
        default=None,
        help=(
            "For --mode init: YYYYMM of the monthly full file (default: current month). "
            "For --mode diff: YYYYMMDD of the diff file (default: today)."
        ),
    )
    args = parser.parse_args()

    client = bigquery.Client(project=PROJECT_ID)

    if args.mode == "init":
        yyyymm = args.date or datetime.now().strftime("%Y%m")
        logger.info(f"=== INIT load: {yyyymm} ===")
        init_load(client, yyyymm)

    elif args.mode == "diff":
        yyyymmdd = args.date or datetime.now().strftime("%Y%m%d")
        logger.info(f"=== DIFF load: {yyyymmdd} ===")
        diff_load(client, yyyymmdd)


if __name__ == "__main__":
    main()
