
# aws_conn_tray

AWS SSO トークンと SSM ポートフォワード（RDS など）を監視し、トレイアイコンと通知で状態を知らせるデスクトップ常駐アプリです。GUI は wxPython 版のみで、認証画面表示は PySide6 + QtWebEngine のサブプロセスを利用します。  
Linux / Windows / macOS の 3OS で動作することを目標にしています（自動起動やポート占有調査など一部は OS ごとに手順が異なります）。

## 主な機能
- トレイアイコン表示（wxPython）。左右クリックでメニューを開く。
- SSO トークン期限の監視（`~/.aws/sso/cache/*.json`）。期限切れ前後で通知・ミニダイアログ表示。
- SSO ログインフローの起動  
  - Qt WebView を最小化で開く（失敗時はブラウザにフォールバック）。  
  - デバイスコード入力画面を開き、`127.0.0.1/.../oauth/callback` を検知したら自動クローズ。
- SSM ポートフォワード (`AWS-StartPortForwardingSessionToRemoteHost`) の起動・監視・自動再接続。
- アイコン色で状態を表示  
  - 緑: トークン有効 & ポートフォワード有効  
  - 黄: トークン有効だが再接続中  
  - 赤: トークン無効（再ログインが必要）
- 設定ダイアログ（wx）で `config/config.json` を編集・バリデート可能。
- ログを `~/.cache/aws_conn_tray/tray.log` に出力。

## ディレクトリ概要
- `tray_app.py` : アプリのエントリポイント。wx アプリを起動。
- `aws_conn_tray/` : 実装本体（SSO/SSM 監視、WebView、GUI）。
- `config/config.example.json` : 設定サンプル。`config/config.json` をここから作る。
- `systemd/aws-conn-tray.service` : systemd --user 用サービス定義。
- `icons/*.png` : トレイアイコン。
- `tests/` : 単体テスト（wx/Qt 依存テストは環境により skip）。

## サポート OS と前提条件
- OS: Linux (Ubuntu 22.x 想定) / Windows 10+ / macOS 12+  
  - Linux: systemd ユニットで自動起動可能。ポート占有調査 (`ss`/`lsof`) やプロセス掃除 (`pgrep`) も利用。  
  - Windows: タスク スケジューラで自動起動を設定してください（ユニットは付属しません）。`taskkill` で残存プロセスを緩やかに終了します。  
  - macOS: LaunchAgent で自動起動を設定する想定（plist は同梱していません）。プロセス掃除は pgrep 経由で行います。
- Python 3.10+ 推奨。
- AWS CLI v2 / Session Manager Plugin がインストール済み。`aws configure sso` を完了していること。
- psutil がインストール済みであること（ポート占有確認・残存プロセス掃除をクロスプラットフォームで行うため必須）。
- 踏み台 EC2 が SSM で到達可能で、そこから対象 DB へ TCP で接続できること。

## セットアップ手順

### 1. 依存パッケージのインストール
```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
```
開発用ツールを入れる場合は `make install-dev` で `pytest/black` なども導入できます。

### 2. フォルダ配置
リポジトリをホーム配下に置く例:
```bash
mv aws-conn-tray ~/aws-conn-tray
cd ~/aws-conn-tray
```

### 3. 設定ファイル作成
```bash
cp config/config.example.json config/config.json
```
主な項目（複数フォワードを `forwards` 配列で持てます）:
- `name` : 表示用ラベル。
- `instance_id` : 踏み台 EC2 のインスタンス ID。
- `profile` / `region` : AWS CLI のプロファイル名・リージョン。
- `local_port` : ローカルで待ち受けるポート。
- `remote_host` / `remote_port` : 接続先ホスト・ポート（例: RDS のホスト/5432）。
- `sso_start_url` : IAM Identity Center の start URL。  
- `sso_login_timeout_seconds` : `aws sso login --no-browser` の待ち時間。
- `sso_auto_refresh_margin_seconds` : 期限がこの秒数以内に迫ったら自動再ログインを試行。
- `tunnel_check_interval_seconds` : トークン/トンネルの監視間隔。

### 4. 起動（手動）
```bash
PYTHONPATH=. .venv/bin/python tray_app.py
```
トレイにアイコンが出て、右クリックでメニューが開けば OK。

### 5. 自動起動

#### Linux (systemd --user)
```bash
mkdir -p ~/.config/systemd/user
cp systemd/aws-conn-tray.service ~/.config/systemd/user/
sed -i "s|/home/YOUR_USERNAME|$HOME|g" ~/.config/systemd/user/aws-conn-tray.service
systemctl --user daemon-reload
systemctl --user enable --now aws-conn-tray.service
```
停止・無効化:
```bash
systemctl --user disable --now aws-conn-tray.service
```

#### Windows (タスク スケジューラ例)
付属のテンプレート: `doc/autostart/windows/task_aws_conn_tray.xml`（文字コード UTF-16）
1. 「タスク スケジューラ」を開き、「基本タスクの作成」。  
2. トリガーは「ログオン時」。  
3. 操作は「プログラムの開始」で以下を指定:  
   - プログラム/スクリプト: `C:\path\to\aws-conn-tray\.venv\Scripts\python.exe`  
   - 引数の追加: `C:\path\to\aws-conn-tray\tray_app.py`
4. 実行ユーザーを現在のユーザーにし、必要なら「最上位の特権で実行する」を有効化。

#### macOS (LaunchAgent 例)
- 付属の plist 雛形: `doc/autostart/macos/aws.conn.tray.plist`（`YOUR_USERNAME` を置換して使用）。  
- `launchctl load ~/Library/LaunchAgents/aws.conn.tray.plist` で読み込み、`launchctl start aws.conn.tray` で起動。  
- `ProgramArguments` の python パスは環境に合わせて変更してください。

### 6. アンインストール
#### Linux
```bash
systemctl --user disable --now aws-conn-tray.service
rm -rf ~/aws-conn-tray ~/.cache/aws_conn_tray
rm ~/.config/systemd/user/aws-conn-tray.service
```
#### Windows
- タスク スケジューラからタスクを削除。
- インストールディレクトリ（例: `C:\aws-conn-tray`）と `C:\Users\<you>\.cache\aws_conn_tray` を削除。

#### macOS
- `launchctl unload ~/Library/LaunchAgents/aws.conn.tray.plist`
- インストールディレクトリと `~/.cache/aws_conn_tray` を削除。

## 動作のポイント
- SSO トークン失効時は右下にミニダイアログが出て「開く」から WebView/ブラウザで再ログインできます。
- トンネルが切れた場合は自動再接続を試行し、ローカルポート衝突が続くと通知で中止を案内します。
- ログは `~/.cache/aws_conn_tray/tray.log`。Qt WebView のストレージは `~/.cache/aws_conn_tray/qt-webview`。

## テスト
- 単体テスト（モック中心）: `make test`
- wx GUI を実際に回す場合: `make test-gui`（`WX_GUI_TEST=1` と DISPLAY が必要）

## 既知の注意
- 環境依存で QtWebEngine が GPU/VA-API で落ちるため、ソフトウェア描画を強制する起動フラグを付与しています。
- 本番運用前に、SSO 設定・踏み台経路・ポート衝突などを検証環境で確認してください。
