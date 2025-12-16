
# aws_conn_tray

AWS SSO トークンと SSM ポートフォワード（RDS など）を監視し、トレイアイコンと通知で状態を知らせるデスクトップ常駐アプリです。GUI は wxPython 版のみで、認証画面表示は PySide6 + QtWebEngine のサブプロセスを利用します。

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

## 前提条件
- Ubuntu 22.x (GNOME) 想定。macOS/Windows でも wx/PySide が動けば利用可。
- Python 3.10+ 推奨。
- AWS CLI v2 / Session Manager Plugin がインストール済み。`aws configure sso` を完了していること。
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

### 5. 自動起動（systemd --user）
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

### 6. アンインストール
```bash
systemctl --user disable --now aws-conn-tray.service
rm -rf ~/aws-conn-tray ~/.cache/aws_conn_tray
rm ~/.config/systemd/user/aws-conn-tray.service
```

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
