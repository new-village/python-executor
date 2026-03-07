# Gemini.md: python-executor

> [!IMPORTANT]
> **AI への指示**: 思考プロセスは英語で行い、コード内のコメントは英語で実装してください。また、実装プラン（`implementation_plan.md`）、ウォークスルー（`walkthrough.md`）、`Readme.md`、および各種説明は日本語で行ってください。


## Project Objective
`python-executor` は、Google Cloud Run Jobs 上で様々な Python ライブラリやスクリプトを効率的かつ柔軟に実行するための汎用実行フレームワークです。

## Key Features
- **Dispatcher Pattern**: `TASK_MODULE` や `TASK_ARGS` といった環境変数を通じて、実行するスクリプトや引数を動的に切り替えます。
- **Cloud Run Jobs Optimized**: 長時間実行や並列処理が必要なバッチジョブに最適化されています。独自のコンテナイメージは作成・管理せず、Google Cloud Run Jobs の[ソースからのデプロイ](https://docs.cloud.google.com/run/docs/quickstarts/jobs/build-create-python)機能を利用します。
- **Modular Design**: 新しいタスクを追加する際、既存のコードへの影響を最小限に抑えられます。

## Technical Stack
- **Language**: Python 3.11+
- **Infrastructure**: Google Cloud Run Jobs
- **CI/CD**: Google Cloud Build
- **Containerization**: Google Cloud Build (source-to-image)
- **Logging**: Google Cloud Logging
