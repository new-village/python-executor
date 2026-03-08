# python-executor

Google Cloud Run Jobs 上で動作する、Pythonジョブの実行用フレームワークです。
コンテナの `ENTRYPOINT` にて `python -m` を利用することで、実行時に指定したモジュールを動的に呼び出せるシンプルな構成となっています。

## 運用コンセプト (全部入り汎用コンテナ)

このプロジェクトは、**「すべてのタスク処理（スクリプト群）を1つのコンテナイメージに詰め込み（全部入り）、Google Artifact Registry にビルド済みコンテナとして置いておく」**という設計です。

*   **Cloud Build (CI/CD)**: GitHub への Push により、最新のソースコードを含む「汎用的なコンテナイメージ」を構築し、Jobを最新化します。
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
export PROJECT_NUMBER=$(gcloud projects describe $PROJECT_ID --format='value(projectNumber)')
```

### 2. Google Cloud API の有効化
```bash
gcloud services enable \
    run.googleapis.com \
    cloudbuild.googleapis.com \
    artifactregistry.googleapis.com
```

### 3. Artifact Registry リポジトリの作成
```bash
gcloud artifacts repositories create $REPOSITORY \
    --repository-format=docker \
    --location=$REGION \
    --description="Cloud Run Job images"
```

### 4. IAM 権限の付与 (Cloud Build用)
```bash
# Cloud Build サービスアカウントに Cloud Run 管理者権限を付加
gcloud projects add-iam-policy-binding $PROJECT_ID \
    --member="serviceAccount:$PROJECT_NUMBER@cloudbuild.gserviceaccount.com" \
    --role="roles/run.admin"

# 実行用サービスアカウントとしての権限借用を許可
gcloud projects add-iam-policy-binding $PROJECT_ID \
    --member="serviceAccount:$PROJECT_NUMBER@cloudbuild.gserviceaccount.com" \
    --role="roles/iam.serviceAccountUser"
```

### 5. 初回デプロイ (コンテナ構築とジョブ作成)
```bash
gcloud builds submit --config cloudbuild.yaml .
```

## ディレクトリ構成
```text
.
├── Dockerfile          # 汎用イメージを構築し、実行時に $TASK_MODULE を呼び出す定義
├── cloudbuild.yaml     # CI/CD定義 (全部入りイメージのBuild, Push, ジョブのUpdate)
├── tasks/              # 実行タスク（処理モジュール）群
│   └── hello.py        # 実行時に TASK_MODULE=tasks.hello として呼び出されるサンプルタスク
└── README.md
```

## ジョブの動的実行 (gcloud コマンド)
環境変数 `TASK_MODULE` を上書きすることで、任意のPythonモジュールを実行できます。

```bash
# デフォルト (tasks.hello) を実行
gcloud run jobs execute $JOB_NAME --region $REGION

# 別モジュール (例: tasks.crawler) を実行
gcloud run jobs execute $JOB_NAME \
  --region $REGION \
  --update-env-vars TASK_MODULE=tasks.crawler
```