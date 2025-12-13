
# aws-rds-tray

AWS SSO トークンと SSM ポートフォワード（RDS 用）を監視する Ubuntu 用トレイ常駐アプリ一式です。

## 機能概要

- Ubuntu のパネル右上（タスクトレイ）に常駐アイコンを表示
- `~/.aws/sso/cache/*.json` から SSO トークンの期限を監視
- トークン期限が 5 分前 / 1 分前 / 期限切れ のタイミングで通知
- SSO ログインページをブラウザで開くメニュー
- AWS SSM のポートフォワード (`AWS-StartPortForwardingSession`) を起動・監視
- ポートフォワードが切れている場合は自動で再接続を試行
- アイコン色で状態を表示
  - 緑: トークン有効 & ポートフォワード有効
  - 黄: トークン有効だがポートフォワード切れ（再接続中）
  - 赤: トークン無効（SSO 再ログインが必要）

## ディレクトリ構成

- `tray_app.py`  
  トレイ常駐アプリ本体（Python3）。

- `config.example.json`  
  設定ファイルのサンプル。`~/.config/aws-rds-tray/config.json` にコピーして使用します。

- `systemd/aws-rds-tray.service`  
  systemd --user 用のサービス定義ファイル。自動起動設定用。

- `icons/green.png`  
  正常状態（トークン & ポートフォワード OK）時のアイコン。

- `icons/yellow.png`  
  トンネル再接続中などの状態のアイコン。

- `icons/red.png`  
  トークン切れなど異常状態のアイコン。

## 事前条件

- Ubuntu 22.x（GNOME）想定
- AWS CLI v2 がインストール済み
- AWS SSO (IAM Identity Center) 設定済み (`aws configure sso` 済)
- 踏み台 EC2 が SSM で接続可能であること
- RDS (PostgreSQL) が踏み台から 5432/TCP で到達可能であること
- Python3 および以下のパッケージ：
  - `python3-gi`
  - `gir1.2-ayatanaappindicator3-0.1` または `gir1.2-appindicator3-0.1`
  - `libnotify-bin`

インストール例（Ubuntu）:

```bash
sudo apt update
sudo apt install -y python3-gi gir1.2-ayatanaappindicator3-0.1 libnotify-bin
```

## セットアップ手順

### 1. このフォルダをホームディレクトリに配置

例:

```bash
mv aws-rds-tray ~/aws-rds-tray
```

### 2. 設定ファイルを配置

`config/config.example.json` を同階層の `config/config.json` にコピーして編集します（アプリはリポジトリ内の `config/config.json` を参照します）。

```bash
cp ~/aws-rds-tray/config/config.example.json ~/aws-rds-tray/config/config.json
nano ~/aws-rds-tray/config/config.json
```

編集ポイント:

- `instance_id`  
  踏み台 EC2（bastion）のインスタンス ID (`i-xxxxxxxxxxxxx`) を指定

- `profile`  
  SSO 用の AWS CLI プロファイル名（例: `PS-Admin-abc`）。

- `region`  
  例: `"ap-northeast-1"`。

- `local_port`  
  ローカル側で開けるポート番号。例: `15432`。

- `remote_port`  
  RDS PostgreSQL のポート番号（通常 `5432`）。

- `sso_start_url`  
  IAM Identity Center (SSO) の "AWS access portal URL"。  
  例: `"https://d-XXXXXXXXXX.awsapps.com/start"`。

### 3. systemd --user サービスを設定

サービス定義ファイルをユーザーの systemd ディレクトリにコピーします。

```bash
mkdir -p ~/.config/systemd/user
cp ~/aws-rds-tray/systemd/aws-rds-tray.service ~/.config/systemd/user/
```

`aws-rds-tray.service` 内の `YOUR_USERNAME` をあなたのユーザー名に置き換えます。

```bash
nano ~/.config/systemd/user/aws-rds-tray.service
# ExecStart の /home/YOUR_USERNAME を /home/あなたのユーザー名 に修正
```

その後、systemd に読み込ませて有効化します。

```bash
systemctl --user daemon-reload
systemctl --user enable --now aws-rds-tray.service
```

これで、ユーザーログイン時に自動でトレイアプリが起動します。

### 4. 初期動作確認

1. すでに `aws sso login --profile <profile>` が成功していることを確認します。
2. パネル右上に小さなアイコン（赤 or 黄 or 緑）が表示されていることを確認します。
3. アイコンを右クリックしてメニューを開きます。
   - 「SSO ログインページを開く」
   - 「ポートフォワード再接続」
   - 「終了」
   が表示されていれば OK です。

### 5. 状態の意味

- アイコンが **緑** の時  
  - SSO トークンは有効
  - SSM ポートフォワードも有効

- アイコンが **黄** の時  
  - SSO トークンは有効
  - ただしポートフォワードが落ちており、自動再接続を試行中

- アイコンが **赤** の時  
  - SSO トークンが無効（期限切れ or 見つからない）
  - `aws sso login --profile <profile>` を実行して再ログインしてください。
  - あるいはメニューの「SSO ログインページを開く」からブラウザでログインページを開いてもよいです。

### 6. ポートフォワードの実際の動作

このアプリは内部で以下のコマンドを実行します。

```bash
aws ssm start-session \
  --target <踏み台EC2のインスタンスID> \
  --document-name AWS-StartPortForwardingSession \
  --parameters portNumber=<remote_port>,localPortNumber=<local_port> \
  --profile <profile> \
  --region <region>
```

そのため、踏み台 EC2 から RDS へ 5432/TCP が通っている必要があります。

### 7. アンインストール / 無効化

自動起動を止めるには：

```bash
systemctl --user disable --now aws-rds-tray.service
```

フォルダごと削除する場合：

```bash
rm -rf ~/aws-rds-tray
rm -rf ~/.config/aws-rds-tray
rm ~/.config/systemd/user/aws-rds-tray.service
```

## 注意事項

- このツールは「簡易的な監視と再接続」を目的としたサンプル実装です。
- 本番運用に導入する前に、検証環境でのテストを推奨します。
- SSO トークンの生成・更新は AWS の仕様変更などにより挙動が変わる可能性があります。
