"""Evaluate ja-entity-parser's parse_address() against BigQuery corpreg data.

Usage:
    # First, export sample via bq CLI:
    bq query --project_id=yata-intelligence --format=csv --max_rows=2000 --nouse_legacy_sql \
      "SELECT prefecture_name, city_name, street_number
       FROM \`yata-intelligence.corpreg.latest\`
       WHERE prefecture_name IS NOT NULL AND prefecture_name != ''
         AND city_name IS NOT NULL AND city_name != ''
         AND street_number IS NOT NULL AND street_number != ''
         AND (address_outside IS NULL OR address_outside = '')
       ORDER BY RAND()
       LIMIT 2000" > /tmp/corpreg_sample.csv

    # Then run evaluation:
    python3 -m tasks.eval_address_parser

Comparison strategy (v2):
    - prefecture / city: exact string match (BQ values are ground truth)
    - block: normalize both BQ street_number and parsed block to canonical form
      before comparing.  BQ stores raw strings like "鍛冶町１丁目７番４号";
      the parser outputs a normalized block like "1-7-4" plus a town prefix.
      We normalize the BQ street_number with normalize_block() too, then
      compare parsed block against the normalized BQ block.
"""
from __future__ import annotations

import csv
import re
from collections import Counter

from ja_entityparser import parse_address
from ja_entityparser.parsers.address_normalize import normalize_block

# ── Config ──────────────────────────────────────────────────────────────────
CSV_PATH = "/tmp/corpreg_sample.csv"
FAILURE_SAMPLE = 20

# Block-only pattern: extract the trailing numeric/structural portion from a
# street_number string so we can normalize it independently of the town prefix.
# Same pattern as used in address.py.
_BLOCK_RE = re.compile(
    r"([0-9０-９]+丁目[0-9０-９]+番[0-9０-９]*号?"
    r"|[0-9０-９]+番地の[0-9０-９]+"
    r"|[0-9０-９]+番地"
    r"|[0-9０-９]+番[0-9０-９]*号?"
    r"|[0-9０-９]+[-－‐ー][0-9０-９]+(?:[-－‐ー][0-9０-９]+)*"
    r"|[0-9０-９]+の[0-9０-９]+"
    r")"
)


def _extract_bq_block(street_number: str) -> str:
    """Extract and normalize the block portion from a raw BQ street_number."""
    m = _BLOCK_RE.search(street_number)
    if m:
        return normalize_block(m.group(0))
    return ""


def evaluate():
    print("=" * 70)
    print("parse_address() 精度評価  (corpreg.latest サンプル)")
    print("=" * 70)

    # ── Load CSV ────────────────────────────────────────────────────────
    print(f"\nCSV読み込み: {CSV_PATH}")
    with open(CSV_PATH, encoding="utf-8") as f:
        reader = csv.DictReader(f)
        rows = list(reader)
    total = len(rows)
    print(f"読み込み完了: {total} 件\n")

    # ── Run parser and compare ──────────────────────────────────────────
    stats = {
        "prefecture_match": 0,
        "city_match": 0,
        "city_match_contains": 0,
        "town_extracted": 0,
        "block_extracted": 0,
        "town_or_block": 0,
        "block_normalized_match": 0,   # NEW: block comparison after normalization
        "full_match": 0,
    }
    failures_pref = []
    failures_city = []
    failures_block = []
    errors = []

    for row in rows:
        bq_pref = row["prefecture_name"]
        bq_city = row["city_name"]
        bq_street = row["street_number"]
        full_addr = f"{bq_pref}{bq_city}{bq_street}"

        try:
            parsed = parse_address(full_addr)
        except Exception as e:
            errors.append((full_addr, str(e)))
            continue

        p_pref = parsed.get("prefecture", "")
        p_city = parsed.get("city", "")
        p_town = parsed.get("town", "")
        p_block = parsed.get("block", "")   # already normalized

        # ── Prefecture ──────────────────────────────────────────────────
        pref_ok = p_pref == bq_pref
        if pref_ok:
            stats["prefecture_match"] += 1
        else:
            failures_pref.append({
                "input": full_addr,
                "expected": bq_pref,
                "got": p_pref,
            })

        # ── City (exact) ────────────────────────────────────────────────
        city_ok = p_city == bq_city
        if city_ok:
            stats["city_match"] += 1
        else:
            failures_city.append({
                "input": full_addr,
                "expected": bq_city,
                "got": p_city,
                "town": p_town,
                "block": p_block,
            })

        # City containment (政令指定都市 etc.)
        if city_ok or (p_city and bq_city.startswith(p_city)) or (p_city and p_city.startswith(bq_city)):
            stats["city_match_contains"] += 1

        # ── Town / block extraction ─────────────────────────────────────
        has_town = bool(p_town)
        has_block = bool(p_block)
        if has_town:
            stats["town_extracted"] += 1
        if has_block:
            stats["block_extracted"] += 1
        if has_town or has_block:
            stats["town_or_block"] += 1

        # ── Block comparison: normalize both sides ──────────────────────
        bq_block_normalized = _extract_bq_block(bq_street)
        block_ok = bool(p_block) and p_block == bq_block_normalized
        if block_ok:
            stats["block_normalized_match"] += 1
        else:
            if pref_ok and city_ok:
                failures_block.append({
                    "input": full_addr,
                    "bq_street": bq_street,
                    "bq_block_normalized": bq_block_normalized,
                    "parsed_town": p_town,
                    "parsed_block": p_block,
                })

        # ── Full match: pref + city + block (normalized) ────────────────
        if pref_ok and city_ok and block_ok:
            stats["full_match"] += 1

    # ── Print results ───────────────────────────────────────────────────
    effective = total - len(errors)
    print("─" * 70)
    print("精度指標")
    print("─" * 70)
    fmt = "{:<50s} {:>6d} / {:>6d}  ({:>6.2f}%)"
    print(fmt.format("都道府県 一致率 (exact)", stats["prefecture_match"], effective,
                      100 * stats["prefecture_match"] / effective))
    print(fmt.format("市区町村 一致率 (exact)", stats["city_match"], effective,
                      100 * stats["city_match"] / effective))
    print(fmt.format("市区町村 一致率 (contains)", stats["city_match_contains"], effective,
                      100 * stats["city_match_contains"] / effective))
    print(fmt.format("町域 抽出率", stats["town_extracted"], effective,
                      100 * stats["town_extracted"] / effective))
    print(fmt.format("番地ブロック 抽出率", stats["block_extracted"], effective,
                      100 * stats["block_extracted"] / effective))
    print(fmt.format("町域 or ブロック 抽出率", stats["town_or_block"], effective,
                      100 * stats["town_or_block"] / effective))
    print(fmt.format("番地ブロック 一致率 [正規化比較・NEW]", stats["block_normalized_match"], effective,
                      100 * stats["block_normalized_match"] / effective))
    print(fmt.format("完全一致率 (pref+city+block正規化)", stats["full_match"], effective,
                      100 * stats["full_match"] / effective))

    if errors:
        print(f"\nパースエラー: {len(errors)} 件")
        for addr, err in errors[:5]:
            print(f"  {addr} → {err}")

    # ── Failure samples ─────────────────────────────────────────────────
    print("\n" + "─" * 70)
    print(f"都道府県 不一致サンプル (max {FAILURE_SAMPLE})")
    print("─" * 70)
    if not failures_pref:
        print("  (なし)")
    for f in failures_pref[:FAILURE_SAMPLE]:
        print(f"  入力: {f['input']}")
        print(f"    期待: {f['expected']}  → パース結果: {f['got']}")

    print("\n" + "─" * 70)
    print(f"市区町村 不一致サンプル (max {FAILURE_SAMPLE})")
    print("─" * 70)
    if not failures_city:
        print("  (なし)")
    for f in failures_city[:FAILURE_SAMPLE]:
        print(f"  入力: {f['input']}")
        print(f"    期待: {f['expected']}  → パース結果: {f['got']}")
        print(f"    (town={f['town']}, block={f['block']})")

    print("\n" + "─" * 70)
    print(f"番地ブロック 不一致サンプル [pref+city OK, max {FAILURE_SAMPLE}]")
    print("─" * 70)
    if not failures_block:
        print("  (なし)")
    for f in failures_block[:FAILURE_SAMPLE]:
        print(f"  入力: {f['input']}")
        print(f"    BQ street: {f['bq_street']}")
        print(f"    BQ block (正規化): {f['bq_block_normalized']}")
        print(f"    parsed: town={f['parsed_town']}  block={f['parsed_block']}")

    # ── City failure pattern analysis ───────────────────────────────────
    print("\n" + "─" * 70)
    print("市区町村 不一致パターン分析")
    print("─" * 70)
    city_pattern_counter = Counter()
    for f in failures_city:
        expected = f["expected"]
        got = f["got"]
        if got and expected.startswith(got):
            city_pattern_counter["政令指定都市（区が残余に流出）"] += 1
        elif not got:
            city_pattern_counter["市区町村マッチなし"] += 1
        elif got and got.startswith(expected):
            city_pattern_counter["過剰マッチ（パーサーが余分に取得）"] += 1
        else:
            city_pattern_counter["その他不一致"] += 1

    for pattern, count in city_pattern_counter.most_common(10):
        print(f"  {pattern}: {count} 件")

    # ── Block failure pattern analysis ──────────────────────────────────
    print("\n" + "─" * 70)
    print("番地ブロック 不一致パターン分析")
    print("─" * 70)
    block_pattern_counter = Counter()
    for f in failures_block:
        bq_block = f["bq_block_normalized"]
        parsed = f["parsed_block"]
        if not parsed:
            block_pattern_counter["block抽出失敗"] += 1
        elif not bq_block:
            block_pattern_counter["BQ側block抽出失敗（street_numberが特殊形式）"] += 1
        else:
            block_pattern_counter["正規化後も不一致"] += 1

    for pattern, count in block_pattern_counter.most_common(10):
        print(f"  {pattern}: {count} 件")

    print("\n" + "=" * 70)
    print("評価完了")
    print("=" * 70)


if __name__ == "__main__":
    evaluate()
