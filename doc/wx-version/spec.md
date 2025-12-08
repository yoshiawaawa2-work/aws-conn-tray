# wx 版仕様まとめ

- 起動切替: 環境変数 `AWS_RDS_TRAY_GUI=wx` で wxPython 版を起動。未設定は従来の GTK 版。
- アプリ構造: `wx_tray.py` の `WxTrayApp` が隠しフレームを持ち、`wx.adv.TaskBarIcon` と `wx.Timer` で監視を実行。
- トレイメニュー: 「⚙️ 設定」「SSO ログイン」「ポートフォワード再接続」「終了」。
- 通知方針: トークン無効時はブラウザ誘導をせず、ミニダイアログ（右下・小さく半透明）を出す。クリックで WebView を前面化して SSO ログインを開始。
- SSO 監視: `tunnel_check_interval_seconds` ごとに SSO トークン残時間を確認。margin 内なら `aws sso login --no-browser` で認可 URL を取得し、ミニダイアログ経由で WebView 再認証を促す（ブラウザフォールバックなし）。
- WebView: `pywebview` で認可 URL を表示（storage_path/user_data_dir を指定してクッキー永続化）。ローカル callback（127.0.0.1/oauth/callback?code=…）で成功判定し自動クローズ。
- トークン無効検知: 無効またはキャッシュなしの場合は赤アイコンにし、ミニダイアログで「開く」を提示。クリックで WebView を表示。通知クリックアクションは無し。
- ポートフォワード監視: SSM セッション状態を定期チェック。切断時は黄色→再接続、正常時は緑。
- 設定ダイアログ: wx.Dialog で `config/config.json` を編集。必須項目/整数のバリデーションを行い、変更があれば保存して tray_app.py を再起動。
- 設定リセット: `config.example.json` をデフォルト値として各数値項目の初期値に利用。
- WebView フォールバック: なし（pywebview が起動できない場合はエラーメッセージのみ）。
- テスト: `make test` で WX 環境が無くても単体テストが走る（GUI 依存部分は skip）。GUI テストは WX 環境で `WX_GUI_TEST=1` をセットして実施。
