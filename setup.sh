#!/usr/bin/env bash
# Gmail Filter セットアップスクリプト
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

echo "=== Gmail Filter セットアップ ==="

# 1. Python venv 作成
if [ ! -d venv ]; then
    python3 -m venv venv
    echo "[OK] venv 作成"
fi

# 2. 依存ライブラリインストール
venv/bin/pip install -q --upgrade pip
venv/bin/pip install -q -r requirements.txt
echo "[OK] 依存ライブラリインストール完了"

# 3. ログファイル作成
if [ ! -f /var/log/gmailfilter.log ]; then
    sudo touch /var/log/gmailfilter.log
    sudo chown kysg:kysg /var/log/gmailfilter.log
    echo "[OK] ログファイル作成"
fi

# 4. APIキー設定ディレクトリ
mkdir -p ~/.config/gmailfilter
if [ ! -f ~/.config/gmailfilter/api_key ]; then
    echo "ANTHROPIC_API_KEY= の値を入力してください:"
    read -r api_key
    echo "export ANTHROPIC_API_KEY=$api_key" > ~/.config/gmailfilter/api_key
    chmod 600 ~/.config/gmailfilter/api_key
    echo "[OK] APIキー保存: ~/.config/gmailfilter/api_key"
fi

# 5. OAuth 認証フロー案内
echo ""
echo "=== Gmail OAuth 認証 ==="
echo "1. Google Cloud Console で OAuth 2.0 クライアント ID を作成"
echo "   https://console.cloud.google.com/"
echo "   - Gmail API を有効化"
echo "   - OAuth クライアント ID（デスクトップアプリ）を作成"
echo "   - JSON をダウンロードして credentials/sugiura.json として保存"
echo ""
echo "2. 認証フローを実行:"
echo "   source ~/.config/gmailfilter/api_key"
echo "   venv/bin/python multi_account.py"
echo ""

# 6. systemd サービス登録
echo "=== systemd サービス登録 ==="
read -rp "systemd サービスを登録しますか？ [y/N] " ans
if [[ "$ans" =~ ^[Yy]$ ]]; then
    sudo cp systemd/gmailfilter.service /etc/systemd/system/
    sudo systemctl daemon-reload
    sudo systemctl enable gmailfilter
    echo "[OK] gmailfilter.service を登録（開始は認証後に: sudo systemctl start gmailfilter）"
fi

echo ""
echo "セットアップ完了。認証後に起動してください。"
