# python-executor

Google Cloud Run Jobs 上で動作する、シンプルな Python ジョブの実行用フレームワークです。
このリポジトリは、将来的な拡張を見据えた最もミニマムな構成になっています。

## 特徴
- **最小限の構成**: `main.py` に記述された処理を実行し、ログ出力を行うだけのシンプルな構造です。
- **Cloud Run Jobs に最適化**: 標準出力による Cloud Logging への最適化が行われています。
- **Cloud Build 対応**: GitHub へのプッシュ時に `cloudbuild.yaml` がトリガーされ、Artifact Registry への Docker イメージの登録および Cloud Run Job としてのデプロイ・実行が自動的に行われます。

## ディレクトリ構成
```text
.
├── Dockerfile          # Cloud Run Job用のコンテナイメージ定義
├── cloudbuild.yaml     # CI/CDのパイプライン定義
├── main.py             # 実際の処理を記述するエントリーポイント
├── requirements.txt    # 必要なPythonライブラリを記載するファイル
└── README.md
```

## 動作確認 (ローカル)
ローカルで動作確認する場合は、以下のコマンドを実行します。
```bash
python main.py
```

出力例:
```
Starting Job Task Index: 0, Attempt: 0
Hello from Cloud Run Job! This is a simple python execution test.
CUSTOM_MESSAGE: No custom message provided.
Job successfully completed.
```

カスタムメッセージを環境変数から渡す場合:
```bash
CUSTOM_MESSAGE="Hello from local" python main.py
```

## Cloud Run Jobs での実行 (Cloud Build)

Google Cloud Build にトリガーが設定されている場合、メインブランチ等に push すると自動的にビルドが走ります。

`cloudbuild.yaml` は以下の処理を行います。
1. **Build**: `Dockerfile` を用いて Docker イメージをビルドします。
2. **Push**: Google Artifact Registry (例: `asia-northeast1` の `my-batch` リポジトリ) へイメージをプッシュします。
3. **Deploy & Execute**: `gcloud run jobs deploy` によりジョブを更新し、コンテナイメージを差し替えた上で直ちに実行 (`--execute-now`) させます。

### 実行ログの確認
実行されたジョブのログは、Google Cloud Console の **Cloud Logging** または **Cloud Run Jobs の実行履歴** から確認できます。

## 今後の拡張について
- 処理を追加する場合は、`main.py` に必要なコードを実装してください。
- 外部ライブラリを追加する場合は、`requirements.txt` にパッケージ名を追加し、`Dockerfile` でのインストール処理 (`RUN pip install ...`) をコメントアウトから有効化してください。