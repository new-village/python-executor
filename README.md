# python-executor

Google Cloud Run Jobs 上で動作する、シンプルな Python ジョブの実行用フレームワークです。
Google Cloud の「ソースからのデプロイ」機能を利用し、Dockerfile なしで実行可能です。

## 事前準備 (Google Cloud 設定)
このプロジェクトを利用するために、Google Cloud 側で以下の設定が必要です。

1.  **API の有効化**:
    -   Cloud Run Admin API (`run.googleapis.com`)
    -   Cloud Build API (`cloudbuild.googleapis.com`)
    -   Artifact Registry API (`artifactregistry.googleapis.com`)
2.  **IAM 権限の付与**:
    -   Cloud Build サービスアカウント（通常は `[Project-Number]@cloudbuild.gserviceaccount.com`）に対し、以下のロールを付与。
        -   `roles/run.admin` (Cloud Run の作成・管理)
        -   `roles/iam.serviceAccountUser` (ランタイムサービスアカウントの権限借用)
3.  **Cloud Build トリガーの設定**:
    -   GitHub リポジトリを連携し、特定のブランチへの Push をトリガーとして `cloudbuild.yaml` を実行するように設定します。

## 特徴
- **極小構成**: `main.py` に処理を記述するだけのシンプルな構造です。
- **Dockerfile 不要**: Google Cloud Build が自動的にソースコードを判別してビルド・デプロイを行います。
- **CI/CD 自動化**: GitHub へのプッシュにより `cloudbuild.yaml` がトリガーされ、デプロイと即時実行が自動化されています。

## ディレクトリ構成
```text
.
├── cloudbuild.yaml     # CI/CD定義 (ソースからのデプロイ)
├── main.py             # ジョブのメインロジック
├── requirements.txt    # 依存ライブラリ (現在は空)
└── README.md
```

## 動作確認 (ローカル)
```bash
python main.py
```

## デプロイと実行
GitHub へ Push すると、Cloud Build トリガーを介して以下の処理が走ります。
1. `gcloud run jobs deploy --source .`: ソースコードからイメージを自動構築し、ジョブをデプロイします。
2. デプロイ完了後、直ちにジョブが実行されます (`--execute-now`)。

実行ログは Google Cloud Console の Cloud Logging から確認してください。