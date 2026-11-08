
# aws_conn_tray

AWS SSO トークンと SSM ポートフォワード（RDS など）を監視し、トレイアイコンと通知で状態を知らせるデスクトップ常駐アプリです。  
GUI 本体は wxPython、認証画面は QtWebEngine（PySide6 サブプロセス）で表示し、インストーラ/アンインストーラ/自動起動ツールは Tk（tkinter）で統一しています。  
Linux / Windows / macOS の 3OS で動作し、インストーラが仮想環境の作成・依存導入・自動起動登録まで行います。

## 主な機能
- トレイ常駐: wxPython のタスクトレイに常駐し、左右クリックでメニューを開く。
- SSO 監視と再ログイン: `~/.aws/sso/cache/*.json` を監視し、期限前後で通知・ミニダイアログ表示。Qt WebView で認証画面を非アクティブ起動し、失敗時はブラウザにフォールバック。device URL の場合は認証コードとコピーを表示。
- SSM ポートフォワード監視: `AWS-StartPortForwardingSessionToRemoteHost` を起動・監視・自動再接続。ローカルポート衝突時はリトライ/通知。
- 設定編集: トレイメニューの「⚙️ 設定」、またはショートカット「AWS Conn Tray - 設定編集」で GUI 編集。必須チェック・デフォルトボタン・ペーストボタン付き。設定保存時に起動中アプリは自動再起動、未起動なら保存のみ。
- 自動起動切替 GUI: 「AWS Conn Tray 自動起動解除」ショートカットで開く Tk 画面から登録/解除をトグル。状態表示付き、`.venv` の Python と `tray_app.py` を使う service/plist/タスクを生成。解除時は起動中のアプリに終了要求を送ります。
- インストーラ/アンインストーラ: Tk ウィザードで前提チェック → venv 作成 → `pip install -r requirements.txt` → 最低限ファイルの配置 → 自動起動登録。アンインストーラはメタデータを参照して安全に削除。
- 単一インスタンス: fasteners（なければポートバインド）でロックし、二重起動を防止。設定編集モードも別ロックで多重起動を抑止。

## ディレクトリ概要
- `tray_app.py` : 本体エントリポイント（`--settings` で設定編集専用起動）。
- `aws_conn_tray/` : 本体実装（wx/SSO/SSM/ロック/インストーラ共通ロジックなど）。
- `installer_ui.py` / `uninstaller_ui.py` : Tk ウィザードのインストーラ/アンインストーラ。
- `installer.py` / `uninstaller.py` : CLI 版のインストーラ/アンインストーラ。
- `autostart_gui.py` : 自動起動登録/解除 GUI・CLI トグル。
- `config/config.example.json` : 設定サンプル。
- `systemd/` : 参考用の systemd 雛形（インストーラは動的生成）。
- `doc/` : ログインフロー説明、wx 仕様、auto-start テンプレート。
- `tests/` : 単体テスト（GUI 依存は条件付き skip）。

## サポート OS と前提条件
- OS: Linux (systemd 想定) / Windows 10+ / macOS 12+  
  - 自動起動は systemd --user / LaunchAgent / タスクスケジューラに対応。テンプレートは venv の Python とインストール先の `tray_app.py` を使う形に更新済み。  
- Python 3.10+（インストーラがインストール先に venv を作成）。  
- AWS CLI v2 / Session Manager Plugin / `aws configure sso` 済み。SSO プロファイルは GUI から既存読み込み＋新規追加が可能。  
- tkinter（インストーラ・自動起動 GUI 用）。Linux は `python3-tk` を OS パッケージで導入。  
- その他のランタイム依存は `requirements.txt` に統合（psutil / fasteners などはここで導入されます）。

## インストール方法
### 1. GUI インストーラ（推奨）
ダブルクリック用ラッパー同梱（Python 未導入時は確認のうえ winget/brew/apt-dnf-zypper で導入を試みます）:
- Windows: `run_installer_windows.bat`
- macOS: `run_installer_macos.command`（必要に応じて `chmod +x`）
- Linux: `run_installer_linux.sh`（必要に応じて `chmod +x`）
```bash
python installer_ui.py           # 実インストール
python installer_ui.py --preview # プレビューモード: ファイルは書き込みません
```
- 前提チェック: Python/AWS CLI/Session Manager/venv 利用可否/SSO プロファイルを確認。不足があれば推奨コマンドを提示（プレビューは表示のみ）。  
- venv 作成 → `pip install -r requirements.txt` を実行。失敗時はクリーンアップして終了。  
- 必要最小限のファイルをインストール先へコピー（テスト・ドキュメント等は除外）。  
- 自動起動とショートカットを作成（各 OS で `.venv` の Python を使うよう生成）。  
- インストールメタデータを記録し、アンインストーラが安全に参照できるようにします。

### 2. CLI インストーラ
```bash
python installer.py --install-dir ~/aws-conn-tray
```
GUI と同じフローを非対話で実行します。

### 3. 手動セットアップ（開発向け）
```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt -r requirements-dev.txt
cp config/config.example.json config/config.json
PYTHONPATH=. python tray_app.py
```
Linux で tkinter が無い場合は `sudo apt-get install -y python3-tk` を追加してください。

## 自動起動とショートカット
- インストーラが以下を生成します（いずれもインストール先の `.venv` Python と `tray_app.py` を呼び出します）。  
  - Linux: `~/.config/systemd/user/aws-conn-tray.service` と `~/.local/share/applications/aws-conn-tray/*.desktop`（起動/設定/自動起動解除）。  
- Windows: スタートメニュー配下に 3 つの .lnk（起動/設定/自動起動解除）。タスクスケジューラ登録用のコマンドを自動実行。  
  - macOS: `~/Library/LaunchAgents/aws.conn.tray.plist` と `/Applications/aws-conn-tray` 配下の .command（起動/設定/自動起動解除）。  
- 自動起動のオン/オフは `autostart_gui.py` を起動すると GUI でトグルできます（状態表示つき）。CLI なら `python autostart_gui.py --enable|--disable`。

## 使い方
- 通常起動: `python tray_app.py`（単一インスタンスロックで二重起動を防止）。  
- 設定のみ開く: `python tray_app.py --settings`（本体は起動せず、起動中の場合のみ保存後に再起動）。  
- トレイメニュー:  
  - ⚙️ 設定: GUI で `config/config.json` を編集。保存後、起動中なら自動再起動。  
  - SSO ログイン: 認可 URL 取得 → WebView（失敗時はブラウザ）でログイン。  
  - 全フォワード再接続 / 個別再接続: ポートフォワードを再起動。  
  - 自動起動解除して終了（最終手段）: 自動起動を解除してから終了。  
  - 終了: アプリを終了。
- SSO/フォワード状態はアイコン色で表示（緑/黄/赤）。期限が近い場合は自動で再ログインを試行し、ミニダイアログと通知を出します（device URL では認証コードを表示）。

## 設定項目（config/config.json）
- `forwards`: 複数のフォワード定義を配列で保持。  
  - `name`: 表示名（タブ名・メニュー名）。  
  - `instance_id`: 踏み台 EC2 のインスタンス ID。  
  - `profile` / `region`: AWS CLI プロファイル名とリージョン。  
  - `remote_host` / `remote_port`: 接続先ホスト/ポート（例: RDS ホスト/5432）。  
  - `local_port`: ローカル待受ポート。衝突時はリトライ。  
- グローバル:  
  - `sso_start_url`: IAM Identity Center の start URL。  
  - `sso_login_timeout_seconds`: `aws sso login --no-browser` の待ち時間。  
  - `sso_auto_refresh_margin_seconds`: 期限がこの秒数以内なら自動再ログインを開始。  
  - `tunnel_check_interval_seconds`: トークン/トンネルの監視頻度（秒）。  
GUI のデフォルトボタン/ペーストボタンを併用すると入力が簡単です。

## アンインストール
- GUI アンインストーラ: `python uninstaller_ui.py`（ダブルクリック用ラッパー: Windows `run_uninstaller_windows.bat` / macOS `run_uninstaller_macos.command` / Linux `run_uninstaller_linux.sh`）  
- CLI: `python uninstaller.py --install-dir <path>`  
- インストールメタデータを確認し、自動起動解除 → ショートカット削除 → インストール先削除 → メタデータ削除の順に安全にクリーンアップします。プレビューモードも利用可能です（`--preview`）。

## テスト
- 単体テスト: `make test`（WX/Qt 依存は環境により skip）。  
- GUI を実際に動かす検証は `WX_GUI_TEST=1 make test-gui` を使用（DISPLAY/Xvfb が必要）。  
- インストーラ/アンインストーラはプレビューモードで副作用なく確認できます。

## 既知の注意・トラブルシュート
- QtWebEngine が GPU 周りで落ちる環境ではソフトウェア描画を強制しています。認証画面が開かない場合は通知/ミニダイアログの「開く」からブラウザでログインしてください。
- `python installer_ui.py --preview` ではファイルはコピーされません。実行ログで前提チェックや生成コマンドのみ確認できます。
- 二重起動が検知された場合は通知して起動を中止します（fasteners が無い場合もソケットロックで代替）。  
- tkinter が無い環境ではインストーラ/自動起動 GUI が起動しません。Linux では `python3-tk` を追加してください。
