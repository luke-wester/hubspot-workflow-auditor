#!/usr/bin/env bash
cd "$HOME/Documents/hubspot-mini-starter"
source .venv/bin/activate
set -a
source .env
set +a
python run_audit.py
