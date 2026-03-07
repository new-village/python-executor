---
name: jpcorpreg-downloader
description: 日本の法人番号公表サイトから法人情報をダウンロードします。
---

# jpcorpreg-downloader

`jpcorpreg` ライブラリを使用して、国税庁の法人番号公表サイトから最新の法人情報を取得します。

## 使い方

このスキルは、`python-executor` フレームワークを通じて実行されます。

### 全件取得

特定の都道府県の法人情報を全件取得します。

```bash
export TASK_MODULE=skills.jpcorpreg-downloader.scripts.download
export TASK_ARGS='{"prefecture": "Shimane"}'
python main.py
```

### 差分取得

指定した日付以降の更新情報を取得します。

```bash
export TASK_MODULE=skills.jpcorpreg-downloader.scripts.download
export TASK_ARGS='{"date": "20260220"}'
python main.py
```

### 全件保存 (Parquet)

全国の法人情報を取得し、`/data` ディレクトリに Parquet 形式で保存します。低メモリ環境（Cloud Run Jobs 等）に対応するため、都道府県ごとに逐次書き込みを行います。

```bash
export TASK_MODULE=skills.jpcorpreg-downloader.scripts.save_registry
export TASK_ARGS='{"data_dir": "/data"}'
python main.py
```

出力ファイル名: `/data/corporate_registry_nta_YYYYMM.parquet`

### パラメータ

- `prefecture` (str, optional): 都道府県名（例: "Tokyo", "Shimane"）。指定しない場合は全国分を取得します。
- `date` (str, optional): 差分取得の基準日（YYYYMMDD）。指定すると差分取得モードになります。
- `column_mapping` (str, default="english"): カラム名を英語にする場合は "english"、日本語にする場合は "japanese"。
- `data_dir` (str, default="/data"): `save_registry` タスクの保存先ディレクトリ。
