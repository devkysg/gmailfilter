# gmailfilter

Gmail の受信トレイを **宣言的YAMLルール + AI** で自動フィルタリングし、Discord / Google Chat へ重要メールを通知する Python サービスです。VPS 上で systemd サービスとして動作します。

AI プロバイダーは **Anthropic（Claude Haiku）** と **OpenAI（GPT シリーズ）** を `config.yaml` の1行変更で切り替えられます。

## アーキテクチャ

```
Gmail API (5分ポーリング)
        ↓
  watcher.py  ─── config.yaml を読み込み
        ↓
  rule_engine.py
    ├─ 即決ルール (From/件名/ヘッダ) → actions.py → Gmailラベル付け
    └─ ルール未一致 → ai_judge.py
                         ↓
                   AI API（Anthropic または OpenAI）
                   ┌─ importance: high → filter/AI-high ラベル + 通知
                   ├─ importance: medium → ラベルなし
                   └─ importance: low  → filter/AI-low ラベル + 既読化
```

## ファイル構成

```
gmailfilter/
├── config.yaml          # 全設定（ルール・アカウント・通知）
├── watcher.py           # メインサービス（5分ポーリング、systemd対応）
├── rule_engine.py       # YAMLルール評価エンジン
├── ai_judge.py          # Claude Haiku判定 + プロンプトキャッシュ
├── actions.py           # Gmailラベル操作 + Discord/GChat通知
├── multi_account.py     # OAuth2トークン管理・自動更新
├── setup.sh             # 初期セットアップスクリプト
├── requirements.txt
├── credentials/         # OAuth client secret（git管理外）
├── tokens/              # 認証トークン（git管理外）
└── systemd/
    └── gmailfilter.service
```

## セットアップ

### 1. Google Cloud Console で認証情報を取得

1. [Google Cloud Console](https://console.cloud.google.com/) でプロジェクトを作成
2. **Gmail API** を有効化
3. **OAuth 同意画面** → 外部 → テストユーザーに自分のアドレスを追加
4. **認証情報** → OAuth 2.0 クライアントID（デスクトップアプリ）を作成
5. JSON をダウンロードして `credentials/<name>.json` として保存

### 2. セットアップスクリプト実行

```bash
git clone https://github.com/devkysg/gmailfilter.git
cd gmailfilter
./setup.sh
```

- Python venv の作成
- 依存ライブラリのインストール
- Anthropic API キーの保存（`~/.config/gmailfilter/api_key`）

### 3. Gmail OAuth 認証

```bash
source ~/.config/gmailfilter/api_key
python multi_account.py
```

表示されたURLをブラウザで開いて許可 → リダイレクト先のURLを貼り付けてEnter。  
`tokens/<name>.token` に保存され、以後は自動更新されます。

### 4. systemd サービス登録

```bash
sudo cp systemd/gmailfilter.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now gmailfilter
sudo systemctl status gmailfilter
```

## 設定ファイル（config.yaml）

### アカウント設定

```yaml
accounts:
  - name: main                          # 任意の識別名
    credentials: credentials/main.json  # OAuth client secret のパス
    token: tokens/main.token            # トークン保存先（自動生成）
```

複数アカウントはリストに追記するだけで対応できます。

### 通知設定

```yaml
notify:
  discord:
    enabled: true
    webhook_url: "https://discord.com/api/webhooks/..."
  google_chat:
    enabled: true
    webhook_url: "https://chat.googleapis.com/v1/spaces/..."
```

### AI設定

`provider` で Anthropic / OpenAI を切り替えられます。

```yaml
# Anthropic（デフォルト）
ai:
  enabled: true
  provider: anthropic
  model: claude-haiku-4-5-20251001
  max_tokens: 256
  cache_ttl_days: 7        # 同一メールの重複判定をRedisでキャッシュ
  notify_on_important: true
```

```yaml
# OpenAI に切り替える場合
ai:
  enabled: true
  provider: openai
  model: gpt-4o-mini       # gpt-5-nano-2025-08-07 なども指定可
  max_tokens: 256
  cache_ttl_days: 7
  notify_on_important: true
```

OpenAI を使う場合は `~/.config/gmailfilter/api_key` に `OPENAI_API_KEY=sk-...` を追加してサービスを再起動してください。

| プロバイダー | プロンプトキャッシュ | 備考 |
|---|---|---|
| Anthropic (Claude) | あり（コスト削減効果大） | デフォルト推奨 |
| OpenAI (GPT) | なし | Redisキャッシュは両方で有効 |

---

## ルールのチューニング

ルールは `config.yaml` の `rules:` セクションに記述します。**上から順に評価され、最初にマッチしたルールが適用されます。**`skip_ai: true` のルールは Claude API を呼ばないためコスト節約になります。

### 基本構造

```yaml
rules:
  - name: "ルール名（ログ表示用）"
    match:
      from: []               # 送信者アドレス（部分一致）
      subject_contains: []   # 件名キーワード（部分一致）
      header_list_unsubscribe: true  # List-Unsubscribeヘッダが存在する場合
    action:
      label: "ラベル名"       # Gmailラベル（/でネスト可）
      mark_read: true         # 既読にする
      skip_inbox: true        # 受信トレイから除外（アーカイブ）
      notify: true            # 通知を送る
    skip_ai: true             # AI判定をスキップ
```

`match` の条件はすべて **AND** 条件です（全て満たした場合にマッチ）。  
`from` や `subject_contains` は **部分一致**です（正規表現不要）。

---

### チューニング事例集

#### 取引先からのメールを即座に通知する

```yaml
  - name: "重要取引先"
    match:
      from: ["@example-client.com", "tanaka@important.co.jp"]
    action:
      label: "filter/重要取引先"
      notify: true
    skip_ai: true
```

#### ECサイトの注文・配送通知を自動整理

```yaml
  - name: "EC注文・配送"
    match:
      from: ["@amazon.co.jp", "@yamato-hd.co.jp", "@post.japanpost.jp",
             "@sagawa.co.jp", "@nttdocomo.com"]
      subject_contains: ["発送", "配達", "ご注文", "お届け", "出荷"]
    action:
      label: "filter/配送"
    skip_ai: true
```

#### ニュースレターを受信トレイから除外

`List-Unsubscribe` ヘッダは大半のメルマガに含まれているため、これだけで広くキャッチできます。

```yaml
  - name: "ニュースレター"
    match:
      header_list_unsubscribe: true
    action:
      label: "filter/newsletter"
      mark_read: true
      skip_inbox: true
    skip_ai: true
```

#### DMARCレポートを自動アーカイブ

```yaml
  - name: "DMARCレポート"
    match:
      subject_contains: ["DMARC", "Report Domain", "Report domain",
                         "Aggregate Report"]
    action:
      label: "filter/DMARC"
      mark_read: true
    skip_ai: true
```

#### 特定サービスのアラートを緊急通知

件名に「障害」「エラー」「CRITICAL」等が含まれるメールを最優先で通知します。

```yaml
  - name: "サービスアラート"
    match:
      subject_contains: ["障害", "CRITICAL", "ERROR", "ALERT", "緊急"]
    action:
      label: "filter/アラート"
      notify: true
    skip_ai: true
```

#### SNS通知をまとめて既読化

```yaml
  - name: "SNS通知"
    match:
      from: ["@facebookmail.com", "@twitter.com", "@linkedin.com",
             "noreply@instagram.com"]
    action:
      label: "filter/SNS"
      mark_read: true
      skip_inbox: true
    skip_ai: true
```

#### 銀行・金融機関を優先表示（AI判定は行うが必ず通知）

`skip_ai: false` にして AI に判定させつつ、重要と判定された場合だけ通知させる例。

```yaml
  - name: "金融機関"
    match:
      from: ["@smbc.co.jp", "@mizuhobank.co.jp", "@americanexpress.com",
             "@visa.co.jp"]
    action:
      label: "filter/金融"
    skip_ai: false   # AIで重要度を判定、high なら自動通知
```

#### ルール未設定のまま AI に任せる場合のコスト目安

| 月間メール数 | AIコール率 | 概算コスト |
|------------|-----------|-----------|
| 1,000通    | 30%（300通）| 約 ¥50/月 |
| 3,000通    | 30%（900通）| 約 ¥150/月 |
| 10,000通   | 30%（3,000通）| 約 ¥450/月 |

ルールを増やして `skip_ai: true` の適用率を上げるとコストを削減できます。

---

## 運用コマンド

```bash
# ログをリアルタイム確認
journalctl -u gmailfilter -f

# 設定変更後の再起動
sudo systemctl restart gmailfilter

# ルールの動作テスト（ドライラン）
venv/bin/python rule_engine.py "件名テスト" "sender@example.com"

# 処理済みIDをリセット（再処理したい場合）
rm processed_ids.txt && sudo systemctl restart gmailfilter
```

## デプロイ方式の比較：venv + systemd vs Docker

本プロジェクトは現在 **venv + systemd** で稼働していますが、Docker化も可能です。  
それぞれのメリット・デメリットを整理します。

### venv + systemd（現在の構成）

```
/home/kysg/projects/gmailfilter/
└── venv/        ← Python 依存関係
systemd/gmailfilter.service  ← プロセス管理
```

| | 内容 |
|---|---|
| **メリット** | セットアップがシンプル（`setup.sh` 1本）|
| | systemd が自動再起動・ログ管理を担う |
| | メモリ消費が最小（~55MB、Dockerオーバーヘッドなし）|
| | OAuth認証フローがそのまま動く |
| | 設定変更 → `systemctl restart` だけで反映 |
| **デメリット** | Pythonバージョンや依存関係がOS環境に依存する |
| | 複数サーバーへの展開時に手順が増える |
| | 他サービスとの依存関係を個別に管理する必要がある |

### Docker + docker-compose

```
docker-compose.yml
├── image: gmailfilter
├── volumes: ./credentials, ./tokens, ./config.yaml
└── env: ANTHROPIC_API_KEY
```

| | 内容 |
|---|---|
| **メリット** | 依存関係をコンテナに完全封じ込め（OS非依存）|
| | VPS上の他のDockerサービス（n8n等）と統一管理できる |
| | `docker compose up -d` だけでどこでも起動できる |
| | イメージをバージョン管理でき、ロールバックが容易 |
| **デメリット** | OAuth初回認証だけはホスト側で事前実行が必要（ブラウザ操作のため）|
| | Docker自体のオーバーヘッド（+20〜30MB程度）|
| | `credentials/`・`tokens/`・`config.yaml` のvolume管理が必要 |
| | ログが `journalctl` から `docker logs` に変わる |

### どちらを選ぶか

```
既存のDockerサービスが多い → Docker の方が統一管理しやすい
このサービス単体で運用   → venv + systemd がシンプルで十分
複数のVPSに展開したい   → Docker の方が再現性が高い
```

現状（VPS 1台・単一サービス）では **venv + systemd で十分**です。  
n8n等のDockerサービスと並べて管理したくなった時点でDocker化を検討するのが現実的です。

### Docker化する場合の構成イメージ

OAuth認証（初回のみホスト側で実行）→ 生成された `tokens/` をマウントして起動。

```yaml
# docker-compose.yml（参考）
services:
  gmailfilter:
    build: .
    restart: unless-stopped
    environment:
      - ANTHROPIC_API_KEY=${ANTHROPIC_API_KEY}
    volumes:
      - ./credentials:/app/credentials:ro
      - ./tokens:/app/tokens
      - ./config.yaml:/app/config.yaml:ro
      - ./processed_ids.txt:/app/processed_ids.txt
```

```dockerfile
# Dockerfile（参考）
FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY *.py ./
CMD ["python", "watcher.py"]
```

---

## セキュリティ注意事項

- `credentials/` と `tokens/` は **絶対にgitにコミットしない**（`.gitignore` で除外済み）
- `config.yaml` の Webhook URL もシークレット情報のため取り扱い注意
- Anthropic API キーは `~/.config/gmailfilter/api_key` に格納し、`chmod 600` で保護
