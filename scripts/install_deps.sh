#!/usr/bin/env bash
set -euo pipefail

# PySimpleGUI を公式 private index から取得するため extra-index-url を付与
python -m pip install --force-reinstall --extra-index-url https://PySimpleGUI.net/install -r requirements.txt -r requirements-dev.txt

echo "Dependencies installed with PySimpleGUI from private index."
