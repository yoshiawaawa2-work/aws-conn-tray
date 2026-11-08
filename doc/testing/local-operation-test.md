# AWS Conn Tray ローカル動作確認手順

この手順は、AWSアカウントを作成しただけで、テスト用リソースがまだない状態から始めます。IAM Identity CenterのSSOログイン、SSMポートフォワード、アプリからの接続を順番に確認します。

> **料金に注意:** EC2の実行時間、ディスク、パブリックIPv4アドレスなどに料金がかかります。また、Free Tier対象アカウントでAWS Organizationsを新規作成すると、従量課金プランに移行し、残っているFree Tierクレジットが直ちに失効します。費用への影響を確認してから進めてください。

## 目次

1. [最初に結論](#1-最初に結論)
2. [全体の流れ](#2-全体の流れ)
3. [始める前に確認すること](#3-始める前に確認すること)
4. [IAM Identity Centerを有効にする](#4-iam-identity-centerを有効にする)
5. [SSO用ユーザーを作る](#5-sso用ユーザーを作る)
6. [テスト用EC2を作る](#6-テスト用ec2を作る)
7. [SSM接続用の権限を設定する](#7-ssm接続用の権限を設定する)
8. [AWS CLIでSSOログインする](#8-aws-cliでssoログインする)
9. [AWS CLIでSSMポートフォワードを確認する](#9-aws-cliでssmポートフォワードを確認する)
10. [アプリから接続を確認する](#10-アプリから接続を確認する)
11. [テスト用リソースを片付ける](#11-テスト用リソースを片付ける)
12. [うまくいかないとき](#12-うまくいかないとき)
13. [参考資料](#13-参考資料)

---

## 1. 最初に結論

**SSOログインだけならEC2は不要です。SSMポートフォワードまで確認する場合は、SSM用のEC2を1台作り、EC2内のテスト用Webサーバーへ接続します。**

テスト先はEC2自身の `127.0.0.1:18080` と `127.0.0.1:18081` です。外部からの受信ポートを開けずに、パソコンの `http://127.0.0.1:15432` と `http://127.0.0.1:15433` から2つの応答を確認します。RDSやSSH接続は必要ありません。

この手順を最後まで行うには、IAM Identity Centerの「組織インスタンス」が必要です。単独アカウントで有効にするとAWS Organizationsが作成される場合があります。**Free Tierや課金条件が変わる可能性を許容できない場合は、Identity Centerの有効化へ進まず、ここで作業を止めてください。**

[目次へ…](#目次)

---

## 2. 全体の流れ

次の順番で進めます。

1. AWSアカウントのリージョンとFree Tierへの影響を確認します。
2. IAM Identity Centerを有効にし、テスト専用のログインユーザーを作ります。
3. 小さなLinux EC2を1台起動し、SSM管理対象にします。
4. そのEC2だけに接続できる権限を作り、テストユーザーに割り当てます。
5. パソコンからAWS CLIでSSOログインし、まずCLIでポートフォワードを試します。
6. アプリをUbuntuにインストールし、2本のポートフォワードを確認します。
7. テスト用EC2などを削除します。

**EC2のリージョン**と**IAM Identity Centerのリージョン**は別々に設定します。AWS CLIのSSO設定にはIdentity Centerのリージョン、EC2へのSSM接続にはEC2のリージョンを使います。

### このアプリを作った背景

このアプリの当初の目的は、VirtualBox上のUbuntu 22からRDSへ接続するとき、SSMポートフォワードを使ってPostgreSQLクライアント（`psql`）から`localhost`を指定できるようにすることでした。SSOの状態やSSMセッションの有効性を毎回手作業で確認して接続し直すのは、システム開発中の負担になります。その確認や再接続をトレイアプリで監視・操作できるようにするために開発されました。

開発の初期には、ChatGPTに作成させたソースコードやドキュメントをZIPにまとめ、Ubuntu上のこのリポジトリへ展開してから、機能追加や修正を続けました。この経緯も含めて、アプリの目的と開発背景を記録しています。

### この手順で確認できる範囲

AWS料金を抑えるため、このテストではRDSを作りません。代わりにEC2内で2つのHTTPテストサーバーを動かし、アプリから別々のローカルポートへ転送します。これでSSO、SSM、複数フォワード、接続確認、再接続操作を試せます。

このHTTPテストでは、実RDSへのネットワーク到達性、PostgreSQLの認証、`psql`コマンドそのものは検証しません。元の用途どおりの確認には、別途RDSを用意し、RDSのセキュリティグループでEC2のセキュリティグループからのPostgreSQL接続（通常TCP 5432）を許可する必要があります。RDSには追加料金がかかるため、本書の最小テストには含めていません。

[目次へ…](#目次)

---

## 3. 始める前に確認すること

### 用意するもの

- AWSコンソールにサインインできるアカウント所有者（rootユーザー）または管理者権限
- AWSアカウントに登録したメールアドレスを受信できること
- このアプリを動かすパソコンの管理者権限（AWS CLIなどのインストールに使用）
- 数十分のテスト時間

AWSアカウントのrootユーザーは初期設定にだけ使い、普段のAWS操作やアプリのSSOログインには使いません。rootユーザーの多要素認証（MFA）が未設定なら、AWSコンソールのアカウントメニューから **Security credentials（セキュリティ認証情報）→Multi-factor authentication (MFA)** を開いて設定してください。rootユーザーのアクセスキーは作成しないでください。

### リージョンを決める

テストするEC2のリージョンを1つ決めます。ここでは例として東京リージョン `ap-northeast-1` を使います。以降の手順では、別のリージョンを選んだ場合、すべての例のリージョンを自分の値に置き換えてください。

IAM Identity Centerを初めて有効にする際に、そのリージョンも選びます。Identity Centerのリージョンは後から簡単に変更できないため、画面上部のリージョン表示を確認してから有効化してください。

### 料金を先に確認する

EC2は停止中もディスク料金などが残ることがあります。パブリックIPv4アドレスにも料金がかかる場合があります。テスト後にインスタンスを「停止」ではなく「終了」し、不要な関連リソースが残っていないか確認します。

テスト用インスタンスタイプは、選択したリージョンで利用可能なら `t4g.nano` を候補にします。これはARM（Graviton）用の小さなサイズです。利用できない場合は、そのリージョンで利用できる小さなLinuxインスタンスのうち、起動画面に表示される料金が低いものを選んでください。`t4g.nano` のメモリは小さいため、起動やSSM接続が不安定な場合は `t4g.micro` へ変更してください。無料利用枠の対象かどうかはアカウントや作成時期によって異なるので、起動前に必ず画面の料金表示を確認してください。

[目次へ…](#目次)

---

## 4. IAM Identity Centerを有効にする

**AWS CLIでAWSアカウントにSSOログインするには、ユーザーをAWSアカウントへ割り当てられる組織インスタンスが必要です。**

### 4.1 Identity Centerの有効化画面を開く

1. AWSコンソールにrootユーザーまたは管理者としてサインインします。
2. 画面上部のリージョンを確認し、Identity Centerを置くリージョンを選択します。例: 東京 `ap-northeast-1`。
3. コンソール上部の検索欄で **IAM Identity Center** を検索し、サービスを開きます。
4. 初めて使う場合は **有効化（Enable）** を選びます。
5. **AWS Organizationsで有効化する組織インスタンス**を選択します。AWSアカウントへユーザーを割り当てる目的では、アカウントインスタンスではなく組織インスタンスを使います。

### 4.2 AWS Organizationsの作成前に止まって確認する

単独のAWSアカウントで組織インスタンスを有効にする場合、画面の案内に従ってAWS Organizationsを作成します。組織を作ると、そのアカウントが組織の管理アカウントになります。

> **重要:** Free Tier対象アカウントでOrganizationsを作成すると、アカウントは有料の従量課金プランに移行し、残っているFree Tierクレジットが直ちに失効します。確認画面に表示される内容を必ず読み、影響を受け入れられない場合は確定ボタンを押さずに中止してください。

AWS Organizationsの作成または有効化を確定し、IAM Identity Centerの画面に戻ります。既に組織インスタンスが有効になっている場合は、重複して有効化せず、そのインスタンスを使用できるか確認します。

### 4.3 Identity CenterのリージョンとStart URLを控える

1. IAM Identity Center画面で、表示中のリージョンが選んだ値であることを確認します。
2. **設定（Settings）** または概要画面から **AWS access portal URL** を確認し、控えます。これは後で `sso_start_url` に設定するURLです。
3. AWS Organizationsの画面で、AWSアカウントID（12桁）も控えます。EC2の作成後に、EC2のインスタンスIDも控えます。

[目次へ…](#目次)

---

## 5. SSO用ユーザーを作る

### 5.1 Identity Centerユーザーを追加する

1. IAM Identity Centerのナビゲーションで **ユーザー（Users）** を選び、**ユーザーを追加（Add user）** を選びます。
2. ユーザー名、メールアドレス、名、姓を入力します。テスト用だと分かるユーザー名（例: `aws-conn-tray-test`）を付けます。
3. メールの受け取り方を選び、確認してユーザーを作成します。
4. 招待メールが届いたら、メール内の手順に従ってパスワードを設定し、ユーザーを有効化します。AWS access portal URLとユーザー名も確認できるようにしておきます。
5. IAM Identity Centerの **設定（Settings）→認証（Authentication）→多要素認証（Multi-factor authentication）** を開き、MFAを要求する設定にします。テストユーザーで初めてサインインしたときに登録画面が表示されたら、認証アプリを選び、画面のQRコードを読み取って確認コードを入力します。

ここで作るユーザーはIAMユーザーやAWSアカウントのrootユーザーではありません。アプリからSSOログインするときに使うIdentity Centerユーザーです。

### 5.2 権限を後で割り当てる準備

次のセクションでEC2を作り、そのインスタンスIDが分かってから、対象をその1台に絞ったSSM権限を作ります。権限のない状態では、テストユーザーでAWSアカウントの操作やSSM接続はできません。

[目次へ…](#目次)

---

## 6. テスト用EC2を作る

**小さなAmazon Linux 2023のEC2を1台作り、受信ルールは追加しません。SSH鍵も不要です。**

### 6.1 EC2の起動画面を開く

1. AWSコンソールで **EC2** を検索して開きます。
2. 画面右上のリージョンを、テスト用EC2のリージョンにします。例: 東京 `ap-northeast-1`。
3. **インスタンスを起動（Launch instance）** を選びます。
4. 名前に `aws-conn-tray-test` と入力します。

### 6.2 OSとインスタンスタイプを選ぶ

1. **アプリケーションおよびOSイメージ（AMI）** で **Amazon Linux 2023** を選びます。
2. `t4g.nano` を使う場合は、**64-bit (Arm)** と表示されたイメージを選びます。Arm用インスタンスとx86用イメージを組み合わせないでください。
3. **インスタンスタイプ**では、利用可能なら `t4g.nano` を選び、画面の料金を確認します。利用できない、またはメモリ不足で不安定な場合は `t4g.micro` など次に小さい対応サイズを選びます。
4. 起動画面に表示される時間単価、無料利用枠の条件、請求見込みを確認します。料金が許容できなければ起動しないでください。

### 6.3 キーペアとネットワークを設定する

1. **キーペア（ログイン）** は **キーペアなしで続行（Proceed without a key pair）** を選びます。この手順ではSSH接続を使いません。
2. **ネットワーク設定**を開きます。既定のVPC（default VPC）と、その中のパブリックサブネットを選びます。既定のVPCが見つからない場合は、同じリージョンのVPCコンソールで **VPC→Your VPCs→Create default VPC** を選び、作成完了後に起動画面へ戻ります。既定のVPCを使うのがこの手順では最も簡単です。
3. **パブリックIPの自動割り当て**を有効にします。EC2がSystems Managerのサービスへ外向きに接続するために使います。
4. セキュリティグループは新しく作成し、インバウンド（受信）ルールを**一つも追加しません**。起動画面にSSH（22番）を許可する行が最初から表示された場合は、その行を削除します。SSH（22番）やテスト用Web（8080番）を許可しないでください。
5. アウトバウンド（送信）は既定の全送信許可を残します。SSM AgentがAWSのSystems ManagerサービスへHTTPS（443番）で接続するために必要です。

パブリックIPv4アドレスを使うため、アドレスの利用料金が発生する場合があります。外部からEC2へ接続するための受信許可を追加する必要はありません。

### 6.4 EC2のIAMロールを設定する

1. 起動画面の **高度な詳細（Advanced details）** を開きます。
2. **IAMインスタンスプロファイル（IAM instance profile）** の選択欄からロールを選びます。
3. 利用できるロールがない場合は、別タブでIAMコンソールを開き、**ロール（Roles）→ロールを作成（Create role）** を選びます。
4. 信頼されたエンティティの種類は **AWSサービス**、ユースケースは **EC2** を選びます。
5. 許可ポリシーにAWS管理ポリシー `AmazonSSMManagedInstanceCore` を追加し、ロール名を `AwsConnTrayTestEc2Role` などにして作成します。
6. EC2起動画面に戻り、作成したロールをインスタンスプロファイルとして選択します。

このロールは**EC2自身がSSM Agentとして動くためのもの**です。人がAWS CLIでSSM接続するときの権限とは別です。人の権限は次のセクションで設定します。

### 6.5 2つのテスト用Webサーバーを起動時に設定する

1. 起動画面の **高度な詳細** にある **ユーザーデータ（User data）** 欄を開きます。
2. 以下を貼り付けます。EC2内で2つの短いHTTP応答を用意します。どちらもEC2自身の `127.0.0.1` だけで待ち受け、外部には公開しません。

   ```bash
   #!/bin/bash
   set -euxo pipefail
   mkdir -p /opt/aws-conn-tray-test/a /opt/aws-conn-tray-test/b
   printf 'SESSION_A_OK\n' > /opt/aws-conn-tray-test/a/index.html
   printf 'SESSION_B_OK\n' > /opt/aws-conn-tray-test/b/index.html
   chmod -R a+rX /opt/aws-conn-tray-test

   cat > /etc/systemd/system/aws-conn-tray-test-a.service <<'EOF'
   [Unit]
   Description=AWS Conn Tray smoke-test HTTP server A
   After=network.target

   [Service]
   Type=simple
   User=nobody
   ExecStart=/usr/bin/python3 -m http.server 18080 --bind 127.0.0.1 --directory /opt/aws-conn-tray-test/a
   Restart=on-failure
   RestartSec=3
   NoNewPrivileges=true

   [Install]
   WantedBy=multi-user.target
   EOF

   cat > /etc/systemd/system/aws-conn-tray-test-b.service <<'EOF'
   [Unit]
   Description=AWS Conn Tray smoke-test HTTP server B
   After=network.target

   [Service]
   Type=simple
   User=nobody
   ExecStart=/usr/bin/python3 -m http.server 18081 --bind 127.0.0.1 --directory /opt/aws-conn-tray-test/b
   Restart=on-failure
   RestartSec=3
   NoNewPrivileges=true

   [Install]
   WantedBy=multi-user.target
   EOF

   systemctl daemon-reload
   systemctl enable --now aws-conn-tray-test-a.service aws-conn-tray-test-b.service
   ```

3. 画面の **ストレージ** では、既定のルートディスク（通常は8 GiB程度）を使います。テスト用の追加ディスクは作りません。
4. 右側の **概要（Summary）** で、インスタンス数が `1`、イメージ、サイズ、リージョン、ネットワークを確認します。
5. 料金をもう一度確認し、問題なければ **インスタンスを起動（Launch instance）** を選びます。

### 6.6 EC2がSSMに登録されたことを確認する

1. EC2の一覧で、`aws-conn-tray-test` の状態が **実行中（Running）** になるまで待ちます。
2. インスタンス詳細からインスタンスID（`i-` から始まる値）とリージョンを控えます。
3. AWSコンソールの検索欄から **Systems Manager** を開きます。EC2と同じリージョンを選びます。
4. **ノード管理（Node Management）→フリートマネージャー（Fleet Manager）** または **マネージドノード（Managed nodes）** を開きます。
5. 対象のインスタンスがオンライン（Online）になるまで数分待ちます。

オンラインにならない場合は、インスタンスロール、パブリックIP、パブリックサブネット、アウトバウンド通信を先に確認してください。

SSMの管理対象に表示されたら、インスタンスの **接続（Connect）→Session Manager→接続（Connect）** からシェルを開き、以下を実行します。

```bash
sudo systemctl status aws-conn-tray-test-a.service --no-pager
sudo systemctl status aws-conn-tray-test-b.service --no-pager
curl --max-time 5 http://127.0.0.1:18080/
curl --max-time 5 http://127.0.0.1:18081/
```

両サービスが `active (running)` で、応答がそれぞれ `SESSION_A_OK` と `SESSION_B_OK` なら、EC2内のテスト先の準備は完了です。ユーザーデータが失敗した場合は、EC2の **Actions→Monitor and troubleshoot→Get system log** を確認するか、SSMシェルで `/var/log/cloud-init-output.log` を確認します。

[目次へ…](#目次)

---

## 7. SSM接続用の権限を設定する

**Identity Centerユーザーには、対象EC2へのセッション開始と自分のセッション終了に必要な権限だけを付けます。** EC2のIAMロールに付けた `AmazonSSMManagedInstanceCore` は、人の権限の代わりにはなりません。

### 7.1 権限セットを作成する

1. IAM Identity Centerのコンソールを、Identity Centerを有効にしたリージョンで開きます。
2. **マルチアカウント権限（Multi-account permissions）→許可セット（Permission sets）** を選びます。
3. **許可セットを作成（Create permission set）** を選び、カスタム許可セットを作成します。
4. 名前を `AwsConnTrayTest` にします。セッション時間は既定値（通常1時間）のままで構いません。
5. インラインポリシー欄に以下のJSONを貼り付けます。3か所の例示値を、実際のアカウントID、EC2リージョン、EC2インスタンスIDに置き換えてください。

   ```json
   {
     "Version": "2012-10-17",
     "Statement": [
       {
         "Sid": "ReadManagedNodeStatus",
         "Effect": "Allow",
         "Action": [
           "ssm:DescribeInstanceInformation",
           "ssm:GetConnectionStatus"
         ],
         "Resource": "*"
       },
       {
         "Sid": "StartPortForwardingToTestInstance",
         "Effect": "Allow",
         "Action": "ssm:StartSession",
         "Resource": [
           "arn:aws:ec2:ap-northeast-1:123456789012:instance/i-0123456789abcdef0",
           "arn:aws:ssm:ap-northeast-1::document/AWS-StartPortForwardingSessionToRemoteHost"
         ]
       },
       {
         "Sid": "ListActiveSessionsForTest",
         "Effect": "Allow",
         "Action": "ssm:DescribeSessions",
         "Resource": "*"
       },
       {
         "Sid": "CloseOwnSessions",
         "Effect": "Allow",
         "Action": [
           "ssm:TerminateSession",
           "ssmmessages:OpenDataChannel"
         ],
         "Resource": "arn:aws:ssm:*:*:session/*",
         "Condition": {
           "StringEquals": {
             "ssm:resourceTag/aws:ssmmessages:session-id": "${aws:userid}"
           }
         }
       }
     ]
   }
   ```

   例の `123456789012` を自分のAWSアカウントIDに、`i-0123456789abcdef0` を実際のインスタンスIDに置き換えます。リージョンは例のままにせず、自分のEC2のリージョンを入れます。JSONの引用符やカンマは消さないでください。

6. 許可セットを作成します。画面にポリシーのエラーが出た場合は、置き換え漏れやJSONの記号を確認します。

### 7.2 ユーザーをAWSアカウントに割り当てる

1. IAM Identity Centerで **AWSアカウント（AWS accounts）** を開きます。
2. テスト対象のAWSアカウント（この手順では自分のアカウント）にチェックを入れ、**ユーザーまたはグループを割り当て（Assign users or groups）** を選びます。
3. **ユーザー（Users）** タブで、前の手順で作ったテストユーザーを選びます。
4. 許可セット一覧で `AwsConnTrayTest` を選び、次へ進みます。
5. 内容を確認して送信します。割り当てが完了するまで数分待ちます。
6. AWS access portalにテストユーザーでログインし、対象AWSアカウントと `AwsConnTrayTest` が表示されることを確認します。

この許可セットは対象EC2へのSSM操作用です。EC2の作成・削除やIAM設定の権限は付けていません。

[目次へ…](#目次)

---

## 8. AWS CLIでSSOログインする

**アプリを起動する前に、同じプロファイルでSSOログインとAWSアカウント確認が成功することを確かめます。**

### 8.1 AWS CLIとSession Manager Pluginをインストールする

この手順はVirtualBox内のUbuntu 22で行います。すでにインストール済みなら、先に次の確認コマンドを実行します。`aws --version`に`aws-cli/2`が表示されればAWS CLI v2は準備済みです。

未導入のツールをダウンロードする場合は、リポジトリ内にファイルができないよう、先に作業用フォルダーへ移動します。

```bash
mkdir -p "$HOME/Downloads/aws-conn-tray-setup"
cd "$HOME/Downloads/aws-conn-tray-setup"
```

```bash
aws --version
session-manager-plugin --version
uname -m
```

#### AWS CLI v2（未導入の場合）

```bash
sudo apt update
sudo apt install -y curl unzip
```

`uname -m`の結果が`x86_64`の場合：

```bash
curl -fsSL https://awscli.amazonaws.com/awscli-exe-linux-x86_64.zip -o awscliv2.zip
unzip awscliv2.zip
sudo ./aws/install
aws --version
```

結果が`aarch64`の場合は、ダウンロード先の`x86_64`を`aarch64`に置き換えます。どちらのアーキテクチャでも、最後の表示に`aws-cli/2`が含まれることを確認します。

#### Session Manager Plugin（未導入の場合）

Ubuntuの`x86_64`向け：

```bash
curl -fsSL \
  https://s3.amazonaws.com/session-manager-downloads/plugin/latest/ubuntu_64bit/session-manager-plugin.deb \
  -o session-manager-plugin.deb
sudo dpkg -i session-manager-plugin.deb
session-manager-plugin --version
```

Ubuntuの`aarch64`では、URL中の`ubuntu_64bit`を`ubuntu_arm64`に置き換えます。インストール後、`session-manager-plugin --version`がバージョンを表示することを確認します。

アプリがAWS CLIを見つけるには、アプリを起動する環境のPATHから`aws`を実行できる必要があります。詳しくは[AWS CLI v2](https://docs.aws.amazon.com/cli/latest/userguide/getting-started-install.html)と[Session Manager Plugin](https://docs.aws.amazon.com/systems-manager/latest/userguide/install-plugin-debian-and-ubuntu.html)の公式手順を参照してください。

### 8.2 SSOプロファイルを作成する

1. ターミナルで次を実行します。

   ```bash
   aws configure sso --profile aws-conn-tray-test
   ```

2. 質問に次のように答えます。

   | 質問 | 入力する値 |
   | --- | --- |
   | SSO session name | `aws-conn-tray-test` |
   | SSO start URL | セクション4で控えたAWS access portal URL |
   | SSO region | セクション4でIdentity Centerを有効にしたリージョン |
   | SSO registration scopes | 表示された既定値をそのまま確定 |

3. ブラウザーが開いたら、セクション5で作ったテストユーザーでサインインします。AWS CLIへのアクセスを求められたら、画面の内容を確認して許可します。
4. ターミナルにAWSアカウントと許可セットが表示されたら、テスト対象アカウントと `AwsConnTrayTest` を選びます。
5. 既定リージョンをEC2のリージョン（例: `ap-northeast-1`）、出力形式を `json` にします。

### 8.3 ログインと接続先の見え方を確認する

次を実行します。

```bash
aws sso login --profile aws-conn-tray-test
aws sts get-caller-identity --profile aws-conn-tray-test
```

2つ目のコマンドが自分のアカウントIDとIdentity Centerのロールを表示すれば、SSOログインに成功しています。表示されたアカウントIDが、テスト用EC2を作ったアカウントと一致することも確認します。

次に、`REGION` と `INSTANCE_ID` を実際の値に置き換えて実行します。

```bash
aws ssm describe-instance-information \
  --filters "Key=InstanceIds,Values=INSTANCE_ID" \
  --region REGION \
  --profile aws-conn-tray-test \
  --query 'InstanceInformationList[0].PingStatus' \
  --output text
```

結果が `Online` なら、AWS CLIからSSM管理対象として認識されています。

[目次へ…](#目次)

---

## 9. AWS CLIでSSMポートフォワードを確認する

**まずCLIから2本のトンネルを同時に作り、AWS側の設定とアプリ側の問題を切り分けます。**

### 9.1 セッションAを開始する

以下のコマンドの `INSTANCE_ID` と `REGION` を実際の値に置き換えます。これはUbuntuの `127.0.0.1:15432` から、EC2内の `127.0.0.1:18080` へ転送します。

```bash
aws ssm start-session \
  --target INSTANCE_ID \
  --document-name AWS-StartPortForwardingSessionToRemoteHost \
  --parameters '{"host":["127.0.0.1"],"portNumber":["18080"],"localPortNumber":["15432"]}' \
  --region REGION \
  --profile aws-conn-tray-test
```

接続が成功すると、ターミナルにセッション開始の表示が出ます。**そのターミナルは開いたままにします。** このコマンドが動いている間だけポートフォワードが有効です。

### 9.2 セッションBを開始する

**別のターミナル**を開き、同じ `INSTANCE_ID` と `REGION` を使って次を実行します。このターミナルも開いたままにします。

```bash
aws ssm start-session \
  --target INSTANCE_ID \
  --document-name AWS-StartPortForwardingSessionToRemoteHost \
  --parameters '{"host":["127.0.0.1"],"portNumber":["18081"],"localPortNumber":["15433"]}' \
  --region REGION \
  --profile aws-conn-tray-test
```

このトンネルは、Ubuntuの `127.0.0.1:15433` からEC2内の `127.0.0.1:18081` へ転送します。AWSでは `AWS-StartPortForwardingSessionToRemoteHost` が、管理対象EC2を経由して指定したホストとポートへ接続します。ここでの `127.0.0.1` はUbuntuではなく、**SSM Agentが動くEC2から見たループバック**です。

### 9.3 2本のトンネルを確認する

さらに別のターミナルを開いて、次を実行します。

```bash
curl --fail --silent --show-error --max-time 10 http://127.0.0.1:15432/
curl --fail --silent --show-error --max-time 10 http://127.0.0.1:15433/
```

期待する結果は、順に `SESSION_A_OK` と `SESSION_B_OK` です。片方だけ表示されない場合は、そのトンネルを実行しているターミナルとEC2内の対応するサービスを確認します。

確認後、セッションAとBを実行している各ターミナルで `Ctrl+C` を押します。**アプリのテスト前に2つとも終了してください。** 同じローカルポートを同時には使えません。

`15432` または `15433` が使用中なら、コマンドと後のアプリ設定のローカルポートを同じ別の未使用番号に変更してください。リモート側の `18080` と `18081` は変更しません。

> Windowsのコマンドプロンプトでは引用符の扱いが異なる場合があります。PowerShellまたはAWS公式のSession Managerポートフォワード手順を使って実行してください。

[目次へ…](#目次)

---

## 10. アプリから接続を確認する

### 10.1 Ubuntu 22へアプリをインストールする

**この手順は、すでにUbuntu上に展開したリポジトリのGUIインストーラを使います。** AWS CLI v2、Session Manager Plugin、セクション8で作ったSSOプロファイルを先に用意してください。

1. Ubuntuのターミナルで、Python 3とTkinter（インストーラ画面用）、venv（アプリ専用Python環境用）が使えることを確認します。

   ```bash
   python3 --version
   python3 -m tkinter
   python3 -m venv --help
   ```

   Tkinterの小さなウィンドウが開けば正常です。確認後はそのウィンドウを閉じます。足りない場合は次を実行し、インストール後に確認をやり直します。

   ```bash
   sudo apt update
   sudo apt install python3-tk python3-venv
   ```

2. ファイルマネージャーで展開済みの`aws-conn-tray`フォルダーを開き、空白部分を右クリックして **端末で開く** を選びます。ターミナルで現在地を確認します。

   ```bash
   pwd
   ```

   表示されたパスの末尾が `aws-conn-tray` で、同じ場所に `installer_ui.py` があることを確認します。

3. インストーラを起動します。

   ```bash
   python3 installer_ui.py
   ```

4. **ようこそ／前提確認**画面で、Python、AWS CLI v2、Session Manager Plugin、SSOプロファイルの不足がないことを確認します。選択肢に `aws-conn-tray-test` が表示されなければ、セクション8のSSOプロファイル作成からやり直します。
5. プロファイルを選択し、画面の **SSOログイン** ボタンを押します。ブラウザーでテストユーザーとして認証し、インストーラに戻ってログイン済みと表示されることを確認してから **次へ** へ進みます。
6. **インストール先**画面では、既定値の `~/aws-conn-tray` を使います（`~`はホームフォルダー、通常は`/home/ユーザー名`です）。初回テスト中にログイン時の自動起動を避けるため、**ログイン時に自動起動を登録する**のチェックを外します。必要ならテスト完了後に自動起動を設定できます。
7. **設定編集**画面に、次の表の2つのフォワードを入力します。最初のフォワードに加え、**フォワード追加**を押して2つ目を作成します。

   | 項目 | フォワードA | フォワードB |
   | --- | --- | --- |
   | 名前 | `smoke-A` | `smoke-B` |
   | インスタンスID | 実際の`i-...` | 同じインスタンスID |
   | プロファイル | `aws-conn-tray-test` | `aws-conn-tray-test` |
   | リージョン | EC2のリージョン（例: `ap-northeast-1`） | 同じリージョン |
   | リモートホスト | `127.0.0.1` | `127.0.0.1` |
   | リモートポート | `18080` | `18081` |
   | ローカルポート | `15432` | `15433` |

8. **SSO Start URL**にはセクション4で控えたAWS access portal URLを入力します。その他の全体設定は、初回テストでは表示された既定値のままにします。
9. **確認**画面でインストール先、自動起動が無効であること、フォワードが2件表示されていることを確認します。誤りがあれば戻って直します。
10. **インストール**を実行し、完了画面で成功を確認します。インストーラは既定では `~/aws-conn-tray` にアプリをコピーし、`.venv`を作成して`requirements.txt`の依存を導入し、設定ファイルを保存します。

### 10.2 インストールしたアプリを起動する

1. セクション9のCLIポートフォワードを両方とも終了したことを確認します。
2. Ubuntuのターミナルで次を実行します。

   ```bash
   "$HOME/aws-conn-tray/.venv/bin/python" "$HOME/aws-conn-tray/tray_app.py"
   ```

3. トレイアイコンが表示されるまで待ちます。SSOログインを求められた場合は、テストユーザーで認証します。
4. トレイメニューのフォワード状態に `smoke-A` と `smoke-B` の両方が表示されることを確認します。
5. 別のターミナルからローカル転送先にアクセスします。

   ```bash
   curl --fail --silent --show-error --max-time 10 http://127.0.0.1:15432/
   curl --fail --silent --show-error --max-time 10 http://127.0.0.1:15433/
   ```

   期待する応答は順に `SESSION_A_OK` と `SESSION_B_OK` です。両方が表示されたら、アプリから別々のSSMポートフォワードを確立できています。

> **現行コードの注意:** このリポジトリのSSM起動・停止処理は、残った`aws ssm start-session`／`session-manager-plugin`をプロセス名で検索してまとめて終了します。そのため、複数フォワードの一方を開始・再接続したときに、他方のセッションまで切れる可能性があります。CLIで2本とも成功するのにアプリで状態が交互に変わる場合は、AWS側の設定だけが原因とは限りません。この確認は、そのアプリ側の挙動も見つけるためのテストです。

### 10.3 片方の切断と再接続を確認する

1. アプリを起動したまま、もう1つのターミナルを開きます。
2. 起動中のSSMセッションを確認します。

   ```bash
   aws ssm describe-sessions \
     --state Active \
     --filters key=Target,value=INSTANCE_ID \
     --region REGION \
     --profile aws-conn-tray-test \
     --query 'Sessions[].[SessionId,Target,Status]' \
     --output table
   ```

   `INSTANCE_ID` と `REGION` を実際の値に置き換えます。アプリが管理する2つのセッションが表示されることを確認します。

3. 表示されたセッションIDのうち、1つだけを指定して終了します。対象がこのテストで開始したセッションであることを確認してください。

   ```bash
   aws ssm terminate-session \
     --session-id "終了するセッションID" \
     --region REGION \
     --profile aws-conn-tray-test
   ```

4. アプリが切断を検知して再接続するか確認します。もう一方のフォワードが維持されるか、切断・再接続されるかも観察し、再接続後に2つの`curl`を再実行します。
5. 自動再接続を確認できない場合も、トレイメニューの **全フォワード再接続** または該当フォワードの **再接続** を選び、手動で再接続できるか確認します。片方の操作がもう一方に影響した場合は、その結果を記録してください。現在のコードにあるプロセス一括クリーンアップによる影響である可能性があります。コード上のプロセス監視と、通信経路の実際の健全性監視は同じではないため、結果を区別して記録してください。
6. 動作確認後、トレイメニューから **終了** を選び、アプリが終了したことを確認します。

### 10.4 開発用にソースから起動する場合

これはインストーラを使わない、ソース編集・テスト用の起動方法です。`requirements-dev.txt`にはテスト用依存関係も含まれます。

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt -r requirements-dev.txt
cp config/config.example.json config/config.json
PYTHONPATH=. python tray_app.py
```

すでに`config/config.json`がある場合は上書きしないでください。`cp`行は設定ファイルがない場合だけ実行し、`config/config.json`を編集して上記2件のフォワードを設定します。通常のインストール先はリポジトリとは別の`~/aws-conn-tray`です。ソースから直接起動する方法と、GUIインストーラでインストールしたアプリの起動方法を混ぜないでください。

[目次へ…](#目次)

---

## 11. テスト用リソースを片付ける

**テスト後は、まずアプリとセッションを終了し、その後EC2を終了します。** EC2を「停止」しただけでは、ディスクなどに料金がかかり続ける場合があります。

1. アプリを終了します。CLIのセッションを使っている場合は、そのターミナルで `Ctrl+C` を押します。
2. EC2コンソールで `aws-conn-tray-test` を選び、**インスタンスの状態（Instance state）→インスタンスを終了（Terminate instance）** を実行します。これは削除操作です。対象がテスト用EC2であることを確認してから実行してください。
3. EC2の状態が **終了済み（Terminated）** になるまで待ちます。
4. **ボリューム（Volumes）** を開き、テスト用EC2に付属したEBSボリュームが残っていないか確認します。残っていて、他の用途で使っていないと確認できたものだけ削除します。
5. **Elastic IP** を割り当てた場合は、テスト用であることを確認して解放します。テスト用にNAT Gatewayを作った場合は、削除して関連するElastic IPも確認します。この手順は既定VPCを使うため、通常NAT Gatewayは作りません。
6. 他の用途で使っていないことを確認してから、テスト用IAMロールとセキュリティグループを削除します。
7. IAM Identity CenterのAWSアカウント割り当てを解除し、不要ならテスト用許可セットとユーザーを削除します。
8. AWS Billing画面で、テスト用に作ったリソースやパブリックIPv4アドレスの料金が残っていないか確認します。

OrganizationsやIAM Identity Centerを無効化・削除すると、他のAWS設定に影響することがあります。組織の削除はこの手順の後片付けに含めず、影響範囲を確認できない場合はそのまま残してください。

[目次へ…](#目次)

---

## 12. うまくいかないとき

| 状況 | 確認すること |
| --- | --- |
| AWS CLIでログインできない | Start URL、Identity Centerのリージョン、ユーザーの初期パスワード設定、AWSアカウントへの割り当てを確認します。 |
| `get-caller-identity` のアカウントIDが違う | `aws configure sso` で選んだAWSアカウントと許可セットを確認します。 |
| `describe-instance-information` の結果が `None` | EC2がSystems ManagerでOnlineか、コマンドのリージョンとインスタンスIDが正しいか確認します。 |
| EC2がSSMでOnlineにならない | `AmazonSSMManagedInstanceCore` を付けたIAMロールがEC2に割り当たっているか、パブリックIP、送信通信、サブネットを確認します。起動直後は数分待ちます。 |
| `AccessDenied` が出る | セクション7の許可セットでアカウントID、EC2リージョン、インスタンスID、ポートフォワード文書名が実際の値と一致するか確認し、割り当て完了まで待ちます。 |
| `session-manager-plugin` が見つからない | プラグインをインストールし、ターミナルを開き直して `session-manager-plugin --version` を実行します。 |
| ポートフォワードは開始するが応答がない | セッションのターミナルを開いたままにしているか、`localPortNumber` とアクセス先のローカルポートが同じかを確認します。EC2内のテストサーバーは `127.0.0.1:18080` と `127.0.0.1:18081` で待ち受けます。 |
| ローカルポートが使用中 | `local_port` と `localPortNumber` をどちらも未使用の番号に変更します。 |
| アプリだけ接続できない | CLIのテストセッションを終了してからアプリを起動し、`profile`、`sso_start_url`、`region`、`instance_id` を確認します。ログはLinux/macOSでは `~/.cache/aws_conn_tray/tray.log` にあります。 |

[目次へ…](#目次)

---

## 13. 参考資料

- [IAM Identity Centerを有効にする](https://docs.aws.amazon.com/singlesignon/latest/userguide/enable-identity-center.html)
- [Identity Centerの既定ディレクトリでユーザーを設定する](https://docs.aws.amazon.com/singlesignon/latest/userguide/quick-start-default-idc.html)
- [ユーザーをAWSアカウントに割り当てる](https://docs.aws.amazon.com/singlesignon/latest/userguide/assignusers.html)
- [AWS CLIでIdentity Center認証を設定する](https://docs.aws.amazon.com/cli/latest/userguide/cli-configure-sso.html)
- [Session Manager用のEC2インスタンス権限](https://docs.aws.amazon.com/systems-manager/latest/userguide/setup-instance-permissions.html)
- [Session ManagerのIAMポリシー例](https://docs.aws.amazon.com/systems-manager/latest/userguide/getting-started-restrict-access-quickstart.html)
- [Session Managerのポートフォワード](https://docs.aws.amazon.com/systems-manager/latest/userguide/session-manager-working-with-sessions-start.html)
- [IAM Identity CenterでMFAを設定する](https://docs.aws.amazon.com/singlesignon/latest/userguide/mfa-configure.html)
- [AWSアカウントのrootユーザーでMFAを有効にする](https://docs.aws.amazon.com/IAM/latest/UserGuide/enable-virt-mfa-for-root.html)
- [EC2起動ウィザード](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/ec2-launch-instance-wizard.html)
- [AWS CLIのインストール](https://docs.aws.amazon.com/cli/latest/userguide/getting-started-install.html)
- [Session Manager Pluginのインストール](https://docs.aws.amazon.com/systems-manager/latest/userguide/session-manager-working-with-install-plugin.html)

[目次へ…](#目次)
