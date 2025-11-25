#!/usr/bin/env bash
set -euo pipefail
ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT_DIR"

info() { echo "[검증] $*"; }
warn() { echo "[경고] $*"; }

info "Python 모듈 문법 검사 (compileall)"
python -m compileall backend

if command -v node >/dev/null 2>&1; then
  info "프런트엔드 스크립트 문법 검사 (node --check)"
  node --check frontend/app.js frontend/login.js frontend/register.js
else
  warn "node 미설치로 JS 문법 검사를 건너뜁니다."
fi

info "pytest 전체 실행"
pytest -q
info "모든 검증이 완료되었습니다."
