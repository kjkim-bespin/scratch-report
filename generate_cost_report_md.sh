#!/usr/bin/env bash
# 비용 리포트 MD 문서 생성 및 스토리지 업로드 — Bash 래퍼
#
# 사용법:
#   DB_HOST=... DB_USER=... DB_PASSWORD=... DB_NAME=... \
#   STORAGE_BUCKET=... \
#   ./generate_cost_report_md.sh [--month YYYY-MM] [--no-upload] [--output-dir DIR]
#
# 환경변수 (DB):
#   DB_HOST, DB_PORT, DB_USER, DB_PASSWORD, DB_NAME
#
# 환경변수 (스토리지):
#   STORAGE_TYPE      s3 | minio (기본값: s3)
#   STORAGE_BUCKET    업로드 대상 버킷 이름  ← 필수
#   STORAGE_ENDPOINT  MinIO 엔드포인트 URL (예: http://minio:9000)
#   AWS_REGION        AWS 리전 (기본값: ap-northeast-2)
#   REPORT_EXPIRE_DAYS  다운로드 링크 유효기간(일) (기본값: 7)

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# ── 의존성 확인 ───────────────────────────────────────────────────
check_dep() {
  if ! command -v "$1" &>/dev/null; then
    echo "[ERROR] 필수 도구 '$1' 이 설치되어 있지 않습니다." >&2
    exit 1
  fi
}

check_dep python3
check_dep psql

# 업로드 모드일 때만 aws CLI 확인
SKIP_UPLOAD=false
for arg in "$@"; do
  [[ "$arg" == "--no-upload" ]] && SKIP_UPLOAD=true
done

if ! $SKIP_UPLOAD; then
  check_dep aws
fi

# ── 환경변수 기본값 ───────────────────────────────────────────────
export DB_HOST="${DB_HOST:-localhost}"
export DB_PORT="${DB_PORT:-5432}"
export DB_USER="${DB_USER:-postgres}"
export DB_PASSWORD="${DB_PASSWORD:-}"
export DB_NAME="${DB_NAME:-postgres}"

export STORAGE_TYPE="${STORAGE_TYPE:-s3}"
export STORAGE_BUCKET="${STORAGE_BUCKET:-}"
export STORAGE_ENDPOINT="${STORAGE_ENDPOINT:-}"
export AWS_REGION="${AWS_REGION:-${AWS_DEFAULT_REGION:-ap-northeast-2}}"
export REPORT_EXPIRE_DAYS="${REPORT_EXPIRE_DAYS:-7}"

# ── Python 스크립트 실행 ──────────────────────────────────────────
exec python3 "${SCRIPT_DIR}/generate_cost_report_md.py" "$@"
