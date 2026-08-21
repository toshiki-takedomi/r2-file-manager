# R2 File Manager

Cloudflare R2のS3互換APIを使用する、ローカル動作のファイルマネージャーです。ブラウザで分割したデータをR2マルチパートアップロードへ直接転送するため、数GBのファイルでもファイル全体の一時コピーを作りません。

## セットアップ

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e .
python -m r2_file_manager
```

起動後、`http://127.0.0.1:8877`（使用中の場合は次の空きポート）が自動的に開きます。`R2_FILE_MANAGER_PORT`で固定ポートも指定できます。

## CLI

無引数では従来どおりWeb画面を起動します。設定済みの接続情報を使って、CLIからバケットやオブジェクトの一覧も取得できます。

```powershell
# ヘルプ
r2-file-manager --help

# バケット一覧
r2-file-manager list-buckets

# バケット直下または指定プレフィックスの一覧
r2-file-manager list-objects my-bucket
r2-file-manager list-objects my-bucket --prefix images/

# サブフォルダーを含む全オブジェクトをJSONで取得
r2-file-manager list-objects my-bucket --recursive --json
```

Web画面で接続設定を保存していない環境では、`R2_ACCOUNT_ID`、`R2_ACCESS_KEY`、`R2_SECRET_ACCESS_KEY`を指定することでもCLIを利用できます。各コマンドのオプションは `r2-file-manager <サブコマンド> --help` で確認できます。

初期設定値には次の環境変数が使用されます。

- `R2_ACCOUNT_ID`
- `R2_ACCESS_KEY`
- `R2_SECRET_ACCESS_KEY`
- `R2_PUBLIC_URL`（任意）
- `CLOUDFLARE_API_TOKEN`（ストレージ使用量表示用・任意）

Secret Access Keyは設定JSONへ保存されず、Windows資格情報マネージャーへ保存されます。通常設定は `%LOCALAPPDATA%\R2 File Manager\config.json`、未完了アップロード情報は同じ場所の `uploads.json` に保存されます。

ストレージ使用量はCloudflare REST APIから取得します。使用量表示を有効にする場合は、AccountのR2読み取り権限を持つCloudflare API Tokenを接続設定へ追加してください。このトークンもWindows資格情報マネージャーへ保存されます。S3用Secret Access KeyからAPI Tokenを復元することはできません。

## 主な機能

- 接続テストと接続情報の安全な更新
- アカウント全体のストレージ使用量・オブジェクト数表示
- バケット一覧、作成、空バケットの削除
- プレフィックスをフォルダーとして扱うオブジェクト一覧
- ブラウザへのファイルダウンロード、公開URLまたは有効期限付き署名URLと`curl`・`wget`例の表示
- 専用ファイルブラウザで複数フォルダーから対象を選び、URL・`curl`・`wget`を一括生成（バケット全体検索対応）
- アップロード済みファイルの移動・名前変更（既存ファイルの上書き確認付き）
- 複数選択削除
- 並列マルチパートアップロード、再試行、キャンセル
- アプリ再起動後の未完了アップロード再開（同じローカルファイルの再選択が必要）

## セキュリティ

サーバーはループバックアドレスにのみバインドされます。変更系APIには起動ごとのトークンが必要です。ログへ資格情報を出力しないでください。資格情報マネージャーが利用できない場合、Secretを平文ファイルへフォールバック保存しません。
