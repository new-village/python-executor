# python-executor

Google Cloud Run Jobs で Python ライブラリやスクリプトを実行するための汎用フレームワークです。

## 概要

このプロジェクトは、環境変数を利用して実行するタスクを動的に切り替えることができる、Docker ベースの実行環境を提供します。

## 特徴

- **柔軟なタスク実行**: 環境変数 `TASK_MODULE` で実行したいスクリプトを指定可能。
- **Google Cloud 連携**: Cloud Run Jobs, Cloud Build などの GCP サービスと親和性が高い設計。
- **拡張性**: `tasks/` ディレクトリに新しいスクリプトを追加するだけで、実行可能なタスクを増やせます。

## 環境変数の設定

このフレームワークは、以下の環境変数を使用して動作を制御します。

### 1. `TASK_MODULE` (必須)

実行する Python モジュールのパスを指定します。
モジュール内には、`run()` または `main()` 関数が定義されている必要があります。

- **例**: `skills.jpcorpreg-downloader.scripts.save_registry`

### 2. `TASK_ARGS` (任意)

実行するモジュールの関数（`run` または `main`）に渡す引数を JSON 形式で指定します。

- **例 (全件保存時の保存先指定)**: `'{"data_dir": "/data"}'`
- **例 (都道府県指定)**: `'{"prefecture": "Shimane"}'`

---

## デプロイ (Google Cloud Run Jobs)

このプロジェクトは、Google Cloud Run Jobs の[ソースからのデプロイ](https://docs.cloud.google.com/run/docs/quickstarts/jobs/build-create-python)機能を利用します。

### 1. 準備

- Google Cloud プロジェクトの課金が有効であること。
- `gcloud` コマンドラインツールがインストール・初期化されていること。
- リポジトリ内の `cloudbuild.yaml` の `substitutions` (ジョブ名、リージョンなど) を必要に応じて書き換えてください。

### 2. ビルドとデプロイ

Google Cloud Build を使用して、ソースコードから直接ジョブを作成・更新します。

```bash
# Cloud Build を使用したデプロイ
# _SERVICE_ACCOUNT を外部から注入することで、設定ファイルへのハードコードを回避します
gcloud builds submit --config cloudbuild.yaml \
  --substitutions=_JOB_NAME="your-job-name",_REGION="asia-northeast1",_SERVICE_ACCOUNT="your-service-account@project-id.iam.gserviceaccount.com"
```

### 3. ジョブの実行

Cloud Run Jobs の「実行」ボタン、または `gcloud` コマンドから実行できます。
**実行時に環境変数を上書き**することで、1つのジョブ設定で多様なタスクを使い分けることが可能です。

```bash
# 特定のタスクを指定してジョブを実行する例
gcloud run jobs execute python-executor-job \
  --region asia-northeast1 \
  --update-env-vars TASK_MODULE=skills.jpcorpreg-downloader.scripts.save_registry,TASK_ARGS='{"data_dir":"/data"}'
```

---

## アーキテクチャ (Dispatcher Pattern)

1. **エントリーポイント (`main.py`)**: コンテナ起動時に呼び出されます。
2. **Dispatcher**: `TASK_MODULE` を読み込み、`importlib` を使用して指定されたモジュールを動的にロードします。
3. **Task Execution**: ロードされたモジュールの `run()` または `main()` 関数に、`TASK_ARGS` の内容をアンパックして渡して実行します。

これにより、新しいスクリプトを追加するたびに Dockerfile やデプロイ設定を修正することなく、環境変数だけで実行内容を切り替えられる柔軟性を実現しています。

---

## ディレクトリ構成

- `main.py`: エントリーポイント。Dispatcher ロジックを含みます。
- `skills/`: 再利用可能なタスク（スキル）の配置ディレクトリ。
- `cloudbuild.yaml`: Cloud Build 用の設定ファイル。
- `requirements.txt`: 依存ライブラリ一覧。
- `GEMINI.md`: 開発ガイドライン（英語での思考プロセス、日本語でのドキュメント作成など）。

## ライセンス

[LICENSE](LICENSE) ファイルを参照してください。