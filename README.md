# python-executor

Google Cloud Run Jobs 上で動作する、Pythonジョブの実行用フレームワークです。
コンテナの `ENTRYPOINT` にて `python -m` を利用することで、実行時に指定したモジュールを動的に呼び出せるシンプルな構成となっています。

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

## 運用コンセプト (全部入り汎用コンテナ)

このプロジェクトの最大の特徴は、**「すべてのタスク処理（スクリプト群）を1つのコンテナイメージに詰め込み（全部入り）、Google Artifact Registry にビルド済みコンテナとして置いておく」**という点です。

*   **Cloud Build (CI/CD)**: GitHub へ Push されると、ソースコード全体（すべてのタスク）を含む「汎用的なコンテナイメージ」をビルドし、Cloud Run Jobs の実体を最新化します。**この時点では処理は実行されません。**
*   **Cloud Run Jobs (実行)**: ジョブを実行するタイミング（手動、Cloud Schedulerによる定期実行など）で、引数（環境変数 `TASK_MODULE`）として「どの処理を実行するか」をコンテナに渡します。

これにより、コンテナを複数管理することなく、1つのイメージからさまざまなタスクを動的に実行できます。

## ディレクトリ構成
```text
.
├── Dockerfile          # 汎用イメージを構築し、実行時に $TASK_MODULE を呼び出す定義
├── cloudbuild.yaml     # CI/CD定義 (全部入りイメージのBuild, Push, ジョブのUpdate)
├── tasks/              # 実行タスク（処理モジュール）群
│   └── hello.py        # 実行時に TASK_MODULE=tasks.hello として呼び出されるサンプルタスク
├── requirements.txt    # 全タスクで共通して利用する依存ライブラリ
└── README.md
```

## 動作確認 (ローカル)
ローカル環境で Docker コンテナ内の挙動をシミュレーションする場合は、以下のように実行します。

```bash
# デフォルトタスクの実行
python -m tasks.hello

# 環境変数を用いた擬似的な実行シミュレーション（コンテナ内での動作と同じ形）
TASK_MODULE=tasks.hello sh -c 'python -m $TASK_MODULE'
```

## ジョブのデプロイ (Cloud Build)
GitHub へ Push すると、Cloud Build トリガーが起動し、以下の処理が行われます。
1. `Dockerfile` を用いてイメージを自動構築・プッシュします。
2. そのイメージを使用して、Cloud Run Jobs の実体である `python-executor-job` を更新・デプロイします。
※ この時、**ジョブは自動的に実行されません。**あくまでコードベースが最新に書き換わるのみです。

## ジョブの動的実行 (gcloud コマンド)
ジョブを実行する際（手動、あるいは Cloud Scheduler 等からの定期トリガーなど）に、環境変数 `--update-env-vars TASK_MODULE=...` を上書きすることで、任意のPythonモジュールを実行できます。

### 実行例
```bash
# 事前にデプロイされているデフォルト設定のままジョブを実行
gcloud run jobs execute python-executor-job --region asia-northeast1

# 実行時にモジュール (例: tasks.crawler) を上書きして実行
gcloud run jobs execute python-executor-job \
  --region asia-northeast1 \
  --update-env-vars TASK_MODULE=tasks.crawler
```

ジョブの実行状況およびログ出力は、Google Cloud Console の Cloud Logging から確認できます。