# wx 版 仕様まとめ（現行）

| 項目 | 詳細 |
| --- | --- |
| 起動切替 | 環境変数 `AWS_RDS_TRAY_GUI=wx` で wx 版を使用。未設定時は従来の GTK 版。|
| アプリ構造 | `wx_tray.py` の `WxTrayApp` が隠しフレーム + `wx.adv.TaskBarIcon`。`wx.Timer` で監視。|
| メニュー | 「⚙️ 設定」「SSO ログイン」「ポートフォワード再接続」「終了」。|
| アイコン色 | 緑=トンネル/トークン正常、黄=再接続中・遷移中、赤=トークン無効/致命エラー。|
| SSO 認証表示 | PySide6 + QtWebEngine をサブプロセス起動（`aws_rds_tray.qwebview_runner`）。認可 URL を開き、`127.0.0.1/.../oauth/callback?code=` を検知して自動クローズ。|
| WebView ストレージ | QtWebEngine の persistent storage を `~/.cache/aws-rds-tray/qt-webview` に設定し、クッキー継続を狙う。|
| フォールバック | Qt WebView 起動に失敗した場合のみデフォルトブラウザで認可 URL を開く。|
| 認可 URL 取得 | `aws sso login --no-browser --profile <profile> --region <region>` を実行し、stdout から認可 URL をパース。POSIX は pty、Windows は pipe で実行。|
| トークン監視 | 監視間隔 `tunnel_check_interval_seconds` ごとに SSO トークン有効期限を確認し、margin (`sso_auto_refresh_margin_seconds`) 内なら再認証を促す。無効・期限切れでアイコン赤。|
| ミニダイアログ | トークン無効/URL取得失敗時に右下に半透明小ダイアログを表示。「開く」→ WebView 起動、「再試行」→ 認可 URL 再取得。テキストは濃色・折り返し済み。|
| 通知 | トークン無効・WebView失敗時などにデスクトップ通知も併用。通知クリックアクションは無し（誘導はミニダイアログ/メニュー）。|
| ポートフォワード | AWS SSM ポートフォワードを起動・監視。切断時は自動再接続。状態をアイコン色に反映。|
| 設定ダイアログ | `config/config.json` の編集・バリデーションを wx.Dialog で提供。必須テキスト/整数チェック。保存時は再起動を案内。|
| デフォルト値 | `config/config.example.json` を参照し、各数値項目の「デフォルト値に戻す」ボタンを提供。|
| ログ | `~/.cache/aws-rds-tray/tray.log` に認可 URL 取得、WebView 起動、トンネル状態、エラーを記録。|
| クロスプラットフォーム | 認可 URL 取得で Windows は pty 非対応のため pipe を使用。WebView は QtWebEngine サブプロセス。|
| テスト | `make test` でモック中心の単体テストが実行可能。GUI 実機テストは DISPLAY/Xvfb で `WX_GUI_TEST=1` を設定して実施。|
