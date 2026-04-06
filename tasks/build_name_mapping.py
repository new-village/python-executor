"""
build_name_mapping.py

Build/update a master name-image mapping table for corporate names
that contain ＿ (U+FF3F fullwidth underscore) in NTA data.

Pipeline:
  1. Query BQ corpreg.latest for records with name_image_id (＿ in name)
  2. Load existing master CSV from GCS (skip already-processed image IDs)
  3. For each new record:
     a. Fetch NTA image (https://www.houjin-bangou.nta.go.jp/image?imageid=XXXXXX)
     b. OCR via gpt-4o-mini (vision)
     c. Web search verification (multiple strategies)
     d. Assign confidence: high / medium / low / unverified
  4. Append new rows to master CSV and upload to GCS

Output:
  gs://yata-raw/master/name_image_mapping.csv

CSV columns:
  name_image_id, original_name, ocr_result, confidence,
  web_confirmed, web_source_url, corporate_number,
  prefecture_name, city_name, created_at, updated_at

Usage:
  python -m tasks.build_name_mapping [--limit N] [--rebuild]
    --limit N    Process at most N new records (default: all)
    --rebuild    Ignore existing master and reprocess all records
"""

import argparse
import base64
import csv
import io
import logging
import os
import time
from datetime import datetime, timezone

import requests
from google.cloud import bigquery, storage
from openai import OpenAI

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
PROJECT_ID    = "yata-intelligence"
DATASET       = "corpreg"
TABLE_LATEST  = f"{PROJECT_ID}.{DATASET}.latest"
GCS_BUCKET    = "yata-raw"
MASTER_PATH   = "master/name_image_mapping.csv"
NTA_IMAGE_URL = "https://www.houjin-bangou.nta.go.jp/image?imageid={image_id}"

OCR_MODEL     = "gpt-4o-mini"   # vision-capable; switch to gpt-5-nano when vision supported
SLEEP_BETWEEN = 0.5             # seconds between API calls

CSV_COLUMNS = [
    "name_image_id",
    "original_name",
    "ocr_result",
    "confidence",
    "web_confirmed",
    "web_source_url",
    "corporate_number",
    "prefecture_name",
    "city_name",
    "created_at",
    "updated_at",
]

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Helpers: GCS
# ---------------------------------------------------------------------------

def load_master_from_gcs(gcs_client: storage.Client) -> dict[str, dict]:
    """Load existing master CSV from GCS. Returns dict keyed by name_image_id."""
    bucket = gcs_client.bucket(GCS_BUCKET)
    blob = bucket.blob(MASTER_PATH)
    if not blob.exists():
        logger.info("Master CSV not found in GCS — starting fresh.")
        return {}

    content = blob.download_as_text(encoding="utf-8")
    reader = csv.DictReader(io.StringIO(content))
    mapping = {}
    for row in reader:
        mapping[row["name_image_id"]] = row
    logger.info(f"Loaded {len(mapping)} existing records from master CSV.")
    return mapping


def save_master_to_gcs(gcs_client: storage.Client, mapping: dict[str, dict]) -> None:
    """Save master CSV to GCS."""
    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=CSV_COLUMNS)
    writer.writeheader()
    for row in mapping.values():
        writer.writerow({col: row.get(col, "") for col in CSV_COLUMNS})

    bucket = gcs_client.bucket(GCS_BUCKET)
    blob = bucket.blob(MASTER_PATH)
    blob.upload_from_string(output.getvalue(), content_type="text/csv; charset=utf-8")
    logger.info(f"Saved {len(mapping)} records to gs://{GCS_BUCKET}/{MASTER_PATH}")


# ---------------------------------------------------------------------------
# Helpers: BQ
# ---------------------------------------------------------------------------

def fetch_candidates(bq_client: bigquery.Client) -> list[dict]:
    """Fetch all ＿-containing records with name_image_id from BQ."""
    query = f"""
    SELECT
        corporate_number,
        name,
        name_image_id,
        prefecture_name,
        city_name
    FROM `{TABLE_LATEST}`
    WHERE REGEXP_CONTAINS(name, r'＿')
      AND name_image_id IS NOT NULL
      AND name_image_id != ''
    ORDER BY name_image_id
    """
    logger.info("Querying BQ for candidates...")
    rows = list(bq_client.query(query).result())
    logger.info(f"Found {len(rows)} candidate records in BQ.")
    return [dict(row) for row in rows]


# ---------------------------------------------------------------------------
# Helpers: Image & OCR
# ---------------------------------------------------------------------------

def fetch_image_base64(image_id: str) -> str | None:
    url = NTA_IMAGE_URL.format(image_id=image_id)
    try:
        r = requests.get(url, timeout=15)
        if r.status_code == 200:
            return base64.b64encode(r.content).decode()
        logger.warning(f"Image fetch HTTP {r.status_code} for {image_id}")
    except Exception as e:
        logger.error(f"Image fetch error for {image_id}: {e}")
    return None


def ocr_company_name(oai_client: OpenAI, image_b64: str, hint_name: str) -> str:
    """OCR company name image via gpt-4o-mini vision."""
    prompt = (
        f"この画像は日本の法人登記における法人名の画像です。"
        f"現在のテキストデータでは「{hint_name}」と記録されており、"
        f"＿（全角アンダーバー）は画像からしか読めない文字を示します。"
        f"画像を正確に読み取り、法人の正式名称を漢字・ひらがな・カタカナ・記号を含めて完全に出力してください。"
        f"法人名のみを出力し、説明は不要です。"
    )
    try:
        resp = oai_client.chat.completions.create(
            model=OCR_MODEL,
            messages=[{
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{image_b64}"}}
                ]
            }],
            max_tokens=100,
            temperature=0,
        )
        return resp.choices[0].message.content.strip()
    except Exception as e:
        logger.error(f"OCR error: {e}")
        return ""


# ---------------------------------------------------------------------------
# Helpers: Web search
# ---------------------------------------------------------------------------

def web_search(brave_key: str, query: str) -> list[dict]:
    """Execute a Brave search and return results list."""
    if not brave_key:
        return []
    try:
        r = requests.get(
            "https://api.search.brave.com/res/v1/web/search",
            params={"q": query, "count": 3},
            headers={"Accept": "application/json", "X-Subscription-Token": brave_key},
            timeout=10,
        )
        if r.status_code == 200:
            return r.json().get("web", {}).get("results", [])
        logger.warning(f"Brave search HTTP {r.status_code} for query: {query}")
    except Exception as e:
        logger.error(f"Brave search error: {e}")
    return []


def verify_by_web(
    brave_key: str,
    ocr_name: str,
    original_name: str,
    corporate_number: str,
    prefecture: str,
    city: str,
) -> tuple[str, bool, str]:
    """
    Multi-strategy web verification.
    Returns (confidence, web_confirmed, web_source_url).

    Strategies (in order):
      1. OCR名 + 都道府県 + 市区町村
      2. 法人番号 単独検索
      3. OCRの＿除去版 + 都道府県
    """
    ocr_clean = ocr_name.replace("＿", "")

    strategies = [
        f"{ocr_name} {prefecture} {city}",
        f"法人番号{corporate_number}",
        f"{ocr_clean} {prefecture} {city}",
    ]

    for query in strategies:
        results = web_search(brave_key, query)
        if not results:
            continue
        time.sleep(0.3)

        for res in results:
            title = res.get("title", "")
            desc  = res.get("description", "")
            url   = res.get("url", "")
            text  = f"{title} {desc}"

            # Full match
            if ocr_name in text or ocr_clean in text:
                logger.info(f"  Web confirmed (full match): {url}")
                return "high", True, url

            # Partial match — OCR result shares prefix/suffix excluding ＿
            # e.g. original=大山＿神社, ocr=大山祇神社 → check 大山 and 神社
            parts = [p for p in original_name.split("＿") if p]
            if all(p in text for p in parts) and len(parts) >= 2:
                logger.info(f"  Web confirmed (partial match): {url}")
                return "medium", True, url

        time.sleep(0.3)

    # No web confirmation
    return "low", False, ""


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=0, help="Max new records to process (0=all)")
    parser.add_argument("--rebuild", action="store_true", help="Ignore existing master and reprocess all")
    args = parser.parse_args()

    # Clients
    bq_client  = bigquery.Client(project=PROJECT_ID)
    gcs_client = storage.Client(project=PROJECT_ID)
    oai_client = OpenAI(api_key=os.environ.get("OPENAI_API_KEY", ""))
    brave_key  = os.environ.get("BRAVE_API_KEY", "")

    if not brave_key:
        logger.warning("BRAVE_API_KEY not set — web verification disabled.")

    # Load existing master
    master = {} if args.rebuild else load_master_from_gcs(gcs_client)
    existing_ids = set(master.keys())

    # Fetch BQ candidates
    candidates = fetch_candidates(bq_client)

    # Filter to new records only
    new_records = [c for c in candidates if c["name_image_id"] not in existing_ids]
    logger.info(f"New records to process: {len(new_records)}")

    if args.limit > 0:
        new_records = new_records[:args.limit]
        logger.info(f"Limited to {len(new_records)} records (--limit {args.limit})")

    # Process each record
    now = datetime.now(timezone.utc).isoformat()
    processed = 0
    errors = 0

    for rec in new_records:
        image_id    = rec["name_image_id"]
        corp_num    = rec["corporate_number"]
        nta_name    = rec["name"]
        prefecture  = rec["prefecture_name"] or ""
        city        = rec["city_name"] or ""

        logger.info(f"Processing [{processed+1}/{len(new_records)}] {image_id} | {nta_name}")

        # Step 1: Fetch image
        img_b64 = fetch_image_base64(image_id)
        if not img_b64:
            master[image_id] = {
                "name_image_id": image_id,
                "original_name": nta_name,
                "ocr_result": "",
                "confidence": "unverified",
                "web_confirmed": "false",
                "web_source_url": "",
                "corporate_number": corp_num,
                "prefecture_name": prefecture,
                "city_name": city,
                "created_at": now,
                "updated_at": now,
            }
            errors += 1
            continue

        # Step 2: OCR
        ocr_result = ocr_company_name(oai_client, img_b64, nta_name)
        time.sleep(SLEEP_BETWEEN)

        if not ocr_result:
            confidence, web_confirmed, web_url = "unverified", False, ""
        else:
            # Step 3: Web verification
            confidence, web_confirmed, web_url = verify_by_web(
                brave_key, ocr_result, nta_name, corp_num, prefecture, city
            )

        master[image_id] = {
            "name_image_id": image_id,
            "original_name": nta_name,
            "ocr_result": ocr_result,
            "confidence": confidence,
            "web_confirmed": str(web_confirmed).lower(),
            "web_source_url": web_url,
            "corporate_number": corp_num,
            "prefecture_name": prefecture,
            "city_name": city,
            "created_at": now,
            "updated_at": now,
        }
        processed += 1

        logger.info(f"  → OCR: {ocr_result!r:25s} | confidence: {confidence} | web: {web_confirmed}")
        time.sleep(SLEEP_BETWEEN)

    # Save master to GCS
    save_master_to_gcs(gcs_client, master)

    # Summary
    total    = len(master)
    high     = sum(1 for r in master.values() if r["confidence"] == "high")
    medium   = sum(1 for r in master.values() if r["confidence"] == "medium")
    low      = sum(1 for r in master.values() if r["confidence"] == "low")
    unverif  = sum(1 for r in master.values() if r["confidence"] == "unverified")

    logger.info("=" * 60)
    logger.info(f"Complete. Processed: {processed}, Errors: {errors}")
    logger.info(f"Master total: {total} | high: {high} | medium: {medium} | low: {low} | unverified: {unverif}")
    logger.info(f"Output: gs://{GCS_BUCKET}/{MASTER_PATH}")


if __name__ == "__main__":
    main()
