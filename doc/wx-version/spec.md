# wx 版 仕様まとめ（現行）

| 項目 | 詳細 |
| --- | --- |
| 起動 | wx 版のみ。`python tray_app.py` で起動（GTK 版は廃止）。systemd --user/LaunchAgent/Task Scheduler はインストーラが `.venv` の Python で動く定義を生成。単一インスタンスロックで二重起動を防止。|
| アプリ構造 | `aws_conn_tray.wx_tray.WxTrayApp` が隠しフレーム + `wx.adv.TaskBarIcon`。`wx.Timer` で周期監視。|
| メニュー | 「⚙️ 設定」「SSO ログイン」「全フォワード再接続」「自動起動解除して終了（最終手段）」「終了」＋状態インジケータと個別再接続メニューを自動生成。設定保存時は起動中アプリを自動再起動。|
| アイコン色 | 緑=トンネル/トークン正常、黄=再接続中・遷移中、赤=トークン無効/致命エラー。最終集計で 1 回だけ色更新。|
| SSO 認証表示 | PySide6 + QtWebEngine サブプロセス（`aws_conn_tray.qwebview_runner`）。認可 URL を開き、`127.0.0.1/.../oauth/callback?code=` を検知して自動クローズ。|
| WebView ストレージ | QtWebEngine の persistent storage を `~/.cache/aws_conn_tray/qt-webview` に設定し、クッキーを永続化。|
| フォールバック | WebView 起動失敗/クラッシュ時はミニダイアログと通知でブラウザ起動を促す。|
| 認可 URL 取得 | `aws sso login --no-browser --profile <profile> --region <region>` を実行し stdout/pty から URL+コードをパース（authorize を優先し、見つからない場合は device URL をフォールバックで採用）。|
| トークン監視/自動更新 | `tunnel_check_interval_seconds` ごとに残り秒数を確認し、`sso_auto_refresh_margin_seconds` 以内なら自動で認可 URL を取得→WebView を開く。|
| ミニダイアログ | tkinter で右下に半透明ダイアログを表示。「開く」で WebView/ブラウザ起動。device URL の場合は認証コードとコピーを表示。|
| 通知 | トークン無効、WebView 失敗、ポート衝突などでデスクトップ通知。クリックでブラウザを開く場合あり。|
| ポートフォワード | AWS SSM ポートフォワードを起動・監視。切断時は自動再接続。ローカルポート衝突時はログ・通知・リトライ（最大 3 回）。|
| 設定ダイアログ | `config/config.json` を複数タブ（forwards 配列）で編集。必須文字列/整数をバリデーション。保存時に自動再起動。設定専用起動（`tray_app.py --settings`）では本体が起動している場合のみ再起動。|
| デフォルト値 | `config/config.example.json` を参照し、数値項目には「デフォルト」ボタンを配置。|
| ログ | `~/.cache/aws_conn_tray/tray.log` に認可 URL 取得、WebView 起動、トンネル状態、エラーを記録。Qt WebView の stdout/stderr も記録。|
| 自動起動/ショートカット | インストーラが OS ごとに自動生成（.desktop/.command/.lnk と systemd/LaunchAgent/Task Scheduler）。自動起動トグル GUI（`autostart_gui.py`）で状態確認と登録/解除が可能。解除時は起動中のアプリに終了要求を送る。|
| 配置/サービス名 | デフォルト配置 `~/aws-conn-tray`。自動生成されるユニット/タスクは `aws-conn-tray` 名で `.venv` の Python と `tray_app.py` を呼ぶ。|
| クロスプラットフォーム | Windows では pty を使わず pipe で認可 URL を取得。WebView は QtWebEngine サブプロセス。tkinter でインストーラ/アンインストーラ/UI トグルも共通化。|
| テスト | `make test` でモック中心の単体テスト。GUI 実機テストは DISPLAY/Xvfb 環境で `make test-gui`（WX_GUI_TEST=1 が必要）。|
