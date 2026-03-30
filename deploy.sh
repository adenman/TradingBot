#!/bin/bash
# Deploy pre-built TradingBot frontend to Bluehost
# Run this AFTER npm run build on Windows
# Usage: bash deploy.sh <path-to-dist>
# Example: bash deploy.sh /home/node/.openclaw/workspace/TradingBot/Frontend/dist

DIST="${1:-/home/node/.openclaw/workspace/TradingBot/Frontend/dist}"
FTP_USER="aden@adenneal.com"
FTP_PASS="god578Aden!"
FTP_HOST="ftp.ocbenji.com"
FTP_PATH="TradingBot"
CURL_OPTS="--ftp-ssl --insecure --silent --show-error"

if [ ! -d "$DIST" ]; then
  echo "❌ dist folder not found at $DIST"
  exit 1
fi

echo "🚀 Uploading to Bluehost from $DIST..."
cd "$DIST"

find . -type f | while read file; do
  clean="${file#./}"
  remote="ftp://$FTP_HOST/$FTP_PATH/$clean"
  curl $CURL_OPTS -u "$FTP_USER:$FTP_PASS" --ftp-create-dirs -T "$file" "$remote"
  echo "  ✓ $clean"
done

echo "✅ Deploy complete! https://adenneal.com/TradingBot/"
