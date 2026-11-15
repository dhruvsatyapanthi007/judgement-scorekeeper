#!/bin/bash
# Rebuild dist/Judgement.app after changing index.html, server.py or app.py.
set -e
cd "$(dirname "$0")"
[ -d .venv ] || { python3 -m venv .venv; .venv/bin/pip install -q pywebview pyinstaller; }
.venv/bin/pyinstaller --noconfirm --clean --windowed --name "Judgement" --icon icon.icns \
  --osx-bundle-identifier com.local.judgement-scorekeeper \
  --add-data "index.html:." --add-data "chart.umd.min.js:." --add-data "fonts:fonts" app.py
rm -rf build
echo "Built: dist/Judgement.app"
