# python-executor

Google Cloud Run Jobs 上で動作する、Pythonジョブの実行用フレームワークです。

## 運用コンセプト

このプロジェクトは、**「すべてのタスク処理（スクリプト群）を1つのコンテナイメージに詰め込み（全部入り）、Google Artifact Registry にビルド済みコンテナとして置いておく」**という設計です。

*   **Cloud Build (CI/CD)**: 最新のソースコードを含む「汎用的なコンテナイメージ」を構築し、Jobを最新化します。
*   **Cloud Run Jobs (実行)**: 実行時に引数 (`--args`) を指定することで、同一イメージから異なるタスクを動的に実行します。

## パイプライン設計 (To-Be v6)

NTA（国税庁）法人番号データの取得・パース・マスター管理を行う2つのパイプラインで構成されます。

設計図: [`images/pipeline_tobe_v6.png`](images/pipeline_tobe_v6.png)

### BigQuery 参照方式

データの重複を避けるため、BigQuery には GCS 上の Parquet ファイルを直接参照する **外部テーブル（External Table）** を使用します。
BQ へのデータロードは行いません。パイプラインは GCS への書き込みのみを担当します。

| BQ テーブル | 参照先 GCS ファイル | 説明 |
|:------------|:--------------------|:-----|
| `corpreg.latest` (EXTERNAL) | `gs://yata-master/corpreg_nta_latest.parquet` | マスター最新版（SQL参照用） |

外部テーブルはファイルが更新されると自動的に最新データが反映されます（再作成不要）。

### 差分フロー（日次バッチ: `tasks.daily_pipeline`）

| Step | 処理 | 出力先 |
|:-----|:------|:-------|
| A | NTA から差分データ fetch | `yata-raw/corpreg_nta_YYYYMMDD.parquet` |
| B | parse & cleanse (ja-entity-parser) | `yata-master/corpreg_nta_diff.parquet`（毎回上書き） |
| C | diff から閉鎖企業を抽出して蓄積 | `yata-master/corpreg_nta_closed.parquet`（append） |
| D | diff を latest に merge (upsert) | `yata-master/corpreg_nta_latest.parquet` |

スケジュール: Cloud Scheduler, 平日 17:00 JST

### 全件フロー（月次バッチ: `tasks.monthly_pipeline`）

| Step | 処理 | 出力先 |
|:-----|:------|:-------|
| 1 | NTA から全件データ fetch | `yata-raw/corpreg_nta_YYYYMM.parquet` |
| 2 | parse & cleanse (ja-entity-parser) | `yata-master/corpreg_nta_active.parquet`（毎回上書き） |
| 3 | closed + active → latest を再構築 | `yata-master/corpreg_nta_latest.parquet`（上書き） |

※ `corpreg_nta_closed.parquet` が存在しない場合（初回）は active をそのまま latest として保存。

### GCS バケット構成

| バケット | ファイル | 説明 |
|:---------|:---------|:-----|
| `yata-raw` | `corpreg_nta_YYYYMMDD.parquet` | 日次差分の生データ |
| `yata-raw` | `corpreg_nta_YYYYMM.parquet` | 月次全件の生データ |
| `yata-master` | `corpreg_nta_diff.parquet` | パース済み差分（毎回上書き） |
| `yata-master` | `corpreg_nta_active.parquet` | パース済み全件アクティブ（月次上書き） |
| `yata-master` | `corpreg_nta_closed.parquet` | 閉鎖企業の累積（日次append） |
| `yata-master` | `corpreg_nta_latest.parquet` | マスター最新版（active + closed）← BQ外部テーブル参照元 |

## タスク一覧

| タスク名 (`ARGS`) | 概要 | 引数 |
|:---|:---|:---|
| `tasks.daily_pipeline` | 日次差分パイプライン (fetch → parse → extract closed → merge) | `YYYYMMDD` (任意、デフォルト: 当日) |
| `tasks.monthly_pipeline` | 月次全件パイプライン (fetch → parse → build latest) | `YYYYMM` (任意、デフォルト: 当月) |
| `tasks.hello` | 動作確認用サンプルタスク | - |
| `tasks.fetch_corpreg_nta_all` | NTA全件取得（単体） | - |
| `tasks.fetch_corpreg_nta_diff` | NTA差分取得（単体） | `YYYYMMDD` (任意) |
| `tasks.parse_corpreg` | NTA Parquet パース（単体） | `YYYYMM` (任意) |

## ジョブの実行 (gcloud コマンド)

```bash
export PROJECT_ID="yata-intelligence"
export REGION="asia-northeast1"
export JOB_NAME="python-executor-job"

# 月次パイプライン
gcloud run jobs execute $JOB_NAME \
  --region $REGION \
  --project $PROJECT_ID \
  --args="tasks.monthly_pipeline","202604" \
  --wait

# 日次パイプライン
gcloud run jobs execute $JOB_NAME \
  --region $REGION \
  --project $PROJECT_ID \
  --args="tasks.daily_pipeline","20260401" \
  --wait
```

実行ステータスの確認:

```bash
# 直近の実行一覧
gcloud run jobs executions list \
  --job $JOB_NAME \
  --region $REGION \
  --project $PROJECT_ID \
  --limit 5

# ログ確認
gcloud logging read \
  "resource.type=cloud_run_job AND resource.labels.job_name=$JOB_NAME" \
  --project $PROJECT_ID \
  --limit 50 \
  --format="value(textPayload)"
```

## ビルド手順 (`cloudbuild-git.yaml`)

`cloudbuild-git.yaml` は GitHub からソースを clone してビルドする方式です。

```bash
cd /path/to/python-executor
COMMIT_SHA=$(git rev-parse HEAD)
PROJECT_ID="yata-intelligence"
JOB_NAME="python-executor-job"

gcloud builds submit \
  --project $PROJECT_ID \
  --config cloudbuild-git.yaml \
  --no-source \
  --substitutions="COMMIT_SHA=${COMMIT_SHA},_JOB_NAME=${JOB_NAME}" \
  --async
```

> **注意:** `COMMIT_SHA` は `git rev-parse HEAD`（フル40文字）を使うこと。短縮SHAだと push ステップが失敗します。

## セットアップガイド (AI・自動化用)

### 1. 環境変数の設定
```bash
export PROJECT_ID=$(gcloud config get-value project)
export REGION="asia-northeast1"
export REPOSITORY="cloud-run-source-deploy"
export IMAGE_NAME="python-executor"
export JOB_NAME="python-executor-job"
export DEPLOY_SA_NAME="cloud-build-sa"
export PROJECT_NUMBER=$(gcloud projects describe $PROJECT_ID --format='value(projectNumber)')
```

### 2. Google Cloud API の有効化
```bash
gcloud services enable \
    run.googleapis.com \
    cloudbuild.googleapis.com \
    artifactregistry.googleapis.com \
    iam.googleapis.com
```

### 3. デプロイ用サービスアカウントの作成

```bash
gcloud iam service-accounts create $DEPLOY_SA_NAME --display-name="Cloud Build Deployer SA"
```

### 4. 権限付与 (IAM Role Bindings)

```bash
for ROLE in roles/run.admin roles/artifactregistry.writer roles/logging.logWriter roles/iam.serviceAccountUser; do
    gcloud projects add-iam-policy-binding $PROJECT_ID \
        --member="serviceAccount:$DEPLOY_SA_NAME@$PROJECT_ID.iam.gserviceaccount.com" \
        --role="$ROLE"
done
```

### 5. Artifact Registry リポジトリの作成
```bash
gcloud artifacts repositories create $REPOSITORY \
    --repository-format=docker \
    --location=$REGION \
    --description="Cloud Run Job images"
```

### 6. 初回デプロイ
```bash
gcloud builds submit --config cloudbuild.yaml --service-account="projects/$PROJECT_ID/serviceAccounts/$DEPLOY_SA_NAME@$PROJECT_ID.iam.gserviceaccount.com" .
```

## BigQuery 外部テーブルの手動再作成（必要時）

外部テーブルは GCS ファイルを参照するだけなので、通常は再作成不要です。
万が一削除された場合は以下で再作成してください。

```bash
# latest（マスター最新版）
bq mk --table \
  --external_table_definition=@PARQUET=gs://yata-master/corpreg_nta_latest.parquet \
  yata-intelligence:corpreg.latest

```

## ディレクトリ構成
```text
.
├── Dockerfile              # コンテナイメージ定義
├── cloudbuild.yaml         # CI/CD定義 (ローカルソース方式)
├── cloudbuild-git.yaml     # CI/CD定義 (GitHub clone方式)
├── requirements.txt        # Python依存パッケージ
├── images/                 # 設計図・ダイアグラム
│   └── pipeline_tobe_v6.png
├── tasks/                  # 実行タスク群
│   ├── __init__.py
│   ├── pipeline_utils.py   # パイプライン共通ユーティリティ
│   ├── daily_pipeline.py   # 日次差分パイプライン
│   ├── monthly_pipeline.py # 月次全件パイプライン
│   ├── hello.py            # サンプルタスク
│   └── ...                 # その他の単体タスク
└── README.md
```
