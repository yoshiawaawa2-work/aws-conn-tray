#!/usr/bin/env bash
set -euo pipefail

python -m pip install -r requirements.txt -r requirements-dev.txt
echo "Dependencies installed."
echo "Note: tkinter is provided by the OS. On Ubuntu: sudo apt-get install -y python3-tk"
