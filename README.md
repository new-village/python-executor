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
| `tasks.renew_corpreg_nta_all` | 国税庁から法人番号データを全件取得し、Parquet形式で保存します。 | 出力先: `/data/corpreg_nta_YYYYMM.parquet`<br>実際の運用では `/data` に Cloud Storage (GCS Fuse) 等のマウントが必要です。 |

## ジョブの動的実行 (gcloud コマンド)
コンテナの引数（`--args`）にモジュール名を渡すことで、任意のPythonモジュールを実行できます。

```bash
# デフォルト (tasks.hello) を実行
gcloud run jobs execute $JOB_NAME --region $REGION

# NTA 法人番号更新タスクを実行 (引数でモジュールを指定)
gcloud run jobs execute $JOB_NAME \
  --region $REGION \
  --args="tasks.renew_corpreg_nta_all"
```