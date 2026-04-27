# python-executor

Google Cloud Run Jobs 上で動作する、Pythonジョブの実行用フレームワークです。

## 運用コンセプト

このプロジェクトは、**「すべてのタスク処理（スクリプト群）を1つのコンテナイメージに詰め込み（全部入り）、Google Artifact Registry にビルド済みコンテナとして置いておく」**という設計です。

*   **Cloud Build (CI/CD)**: 最新のソースコードを含む「汎用的なコンテナイメージ」を構築し、Jobを最新化します。
*   **Cloud Run Jobs (実行)**: 実行時に環境変数 `TASK_MODULE` を指定することで、同一イメージから異なるタスクを動的に実行します。

## セットアップガイド (AI・自動化用)

以下の手順に従って `gcloud` コマンドを実行することで、環境を完全に再現できます。

### 1. 環境変数の設定
```bash
export PROJECT_ID=$(gcloud config get-value project)
export REGION="asia-northeast1"
export REPOSITORY="cloud-run-source-deploy"
export IMAGE_NAME="python-executor"
export JOB_NAME="python-executor-job"
# デプロイ用サービスアカウント名
export DEPLOY_SA_NAME="cloud-build-sa"
# プロジェクト番号の取得
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
# デプロイ用SAへの権限付加
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

## ディレクトリ構成
```text
.
├── Dockerfile          # 汎用イメージの定義
├── cloudbuild.yaml     # CI/CD定義 (Build, Push, Update)
├── tasks/              # 実行タスク（処理モジュール）群
│   └── hello.py        # サンプルタスク
└── README.md
```

## タスク一覧

現在実装されているタスクとその実行要件です。

| タスク名 (`ARGS`) | 概要 | 備考 |
| :--- | :--- | :--- |
| `tasks.hello` | 動作確認用のサンプルタスク。ログに挨拶を出力します。 | - |
| `tasks.fetch_corpreg_nta_all` | 国税庁から法人番号データを全件取得し、Parquet形式で保存します。 | 出力先: `/data/corpreg_nta_YYYYMM.parquet`<br>実際の運用ではメモリ増量(8GB〜)を推奨。 |
| `tasks.fetch_corpreg_nta_diff` | 特定の日付（デフォルトは当日）の差分データを取得し、Parquet形式で保存します。 | 引数: `YYYYMMDD` (任意)<br>出力先: `/data/corpreg_nta_YYYYMMDD.parquet` |
| `tasks.parse_corpreg` | NTA法人登記Parquetの法人名・住所を `ja-entity-parser` でパースし、構造化済みParquetをGCSに出力します。 | 引数: `YYYYMM` (任意、デフォルト: 当月)<br>入力: `gs://yata-raw/corpreg_nta_YYYYMM.parquet`<br>出力: `gs://yata-master/corpreg_parsed_YYYYMM.parquet` |

## ジョブの動的実行 (gcloud コマンド)

コンテナの引数（`--args`）にモジュール名を渡すことで、任意のPythonモジュールを実行できます。

```bash
export PROJECT_ID="yata-intelligence"
export REGION="asia-northeast1"
export JOB_NAME="python-executor-job"

# 動作確認 (tasks.hello)
gcloud run jobs execute $JOB_NAME \
  --region $REGION \
  --project $PROJECT_ID

# NTA 法人番号差分取得（引数で日付を指定）
gcloud run jobs execute $JOB_NAME \
  --region $REGION \
  --project $PROJECT_ID \
  --args="tasks.fetch_corpreg_nta_diff","20260427"

# 法人登記パース（全件スナップショット: gs://yata-raw/corpreg_nta_YYYYMM.parquet → gs://yata-master/corpreg_parsed_YYYYMM.parquet）
gcloud run jobs execute $JOB_NAME \
  --region $REGION \
  --project $PROJECT_ID \
  --args="tasks.parse_corpreg","202603"
```

> **注意:** Cloud Run Job のデフォルト args は `tasks.fetch_corpreg_nta_diff` に設定されています。`--args` を省略するとデフォルトが実行されます。実行中のステータスは以下で確認できます。

```bash
# 直近の実行一覧
gcloud run jobs executions list \
  --job $JOB_NAME \
  --region $REGION \
  --project $PROJECT_ID \
  --limit 5

# 特定実行の詳細
gcloud run jobs executions describe <EXECUTION_NAME> \
  --region $REGION \
  --project $PROJECT_ID

# ログ確認
gcloud logging read \
  "resource.type=cloud_run_job AND resource.labels.job_name=$JOB_NAME" \
  --project $PROJECT_ID \
  --limit 50 \
  --format="value(textPayload)"
```

## イメージのビルド・デプロイ手順

`requirements.txt` や `tasks/` を変更した後は、新しいイメージをビルドして Job を更新する必要があります。

### ビルド方法（`cloudbuild-git.yaml` を使用）

`cloudbuild.yaml`（ローカルソースをアップロードする方式）は権限エラーになるため、
GitHub から clone する `cloudbuild-git.yaml` を使います。

```bash
# main ブランチの HEAD を確認
cd /path/to/python-executor
COMMIT_SHA=$(git rev-parse HEAD)

# ビルド実行（--no-source でローカルソース送信をスキップ）
gcloud builds submit \
  --project $PROJECT_ID \
  --config cloudbuild-git.yaml \
  --no-source \
  --substitutions="COMMIT_SHA=${COMMIT_SHA},_JOB_NAME=${JOB_NAME}" \
  --async

# ビルドステータス確認
gcloud builds list --project $PROJECT_ID --limit 3
```

> **注意:** `COMMIT_SHA` は `git rev-parse HEAD`（フル40文字）を使うこと。短縮SHA（7文字）だと push ステップが失敗します。

### ビルド済みイメージで Job だけ更新する場合

Artifact Registry に既にイメージがある場合（再ビルド不要な場合）は、直接 Job を更新できます。

```bash
# 利用可能なタグ一覧
gcloud artifacts docker tags list \
  asia-northeast1-docker.pkg.dev/$PROJECT_ID/cloud-run-source-deploy/python-executor \
  --project $PROJECT_ID

# Job のイメージを更新
IMAGE="asia-northeast1-docker.pkg.dev/$PROJECT_ID/cloud-run-source-deploy/python-executor:<COMMIT_SHA>"
gcloud run jobs update $JOB_NAME \
  --image $IMAGE \
  --region $REGION \
  --project $PROJECT_ID
```