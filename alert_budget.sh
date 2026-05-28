#!/usr/bin/env bash
# 조직별 예산 한도 임박 알림 스크립트
# 사용률이 임계값 이상인 조직을 감지하여 Google Chat 웹훅 또는 stdout으로 알림을 발송합니다.
#
# 사용법:
#   DB_HOST=... DB_USER=... DB_PASSWORD=... DB_NAME=... ./alert_budget.sh \
#       [--threshold|-t PERCENT] [--webhook|-w WEBHOOK_URL] [--output|-o OUTPUT_DIR]
#
# 환경변수:
#   ALERT_THRESHOLD  알림 임계값(%) — 기본값 90
#   WEBHOOK_URL      Google Chat Incoming Webhook URL

set -euo pipefail

DB_HOST="${DB_HOST:-localhost}"
DB_PORT="${DB_PORT:-5432}"
DB_USER="${DB_USER:-postgres}"
DB_PASSWORD="${DB_PASSWORD:-}"
DB_NAME="${DB_NAME:-postgres}"
WEBHOOK_URL="${WEBHOOK_URL:-}"
ALERT_THRESHOLD="${ALERT_THRESHOLD:-90}"
OUTPUT_DIR=""

DB_URL="postgresql://${DB_USER}:${DB_PASSWORD}@${DB_HOST}:${DB_PORT}/${DB_NAME}"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --threshold|-t) ALERT_THRESHOLD="$2"; shift 2 ;;
    --webhook|-w)   WEBHOOK_URL="$2";     shift 2 ;;
    --output|-o)    OUTPUT_DIR="$2";      shift 2 ;;
    *) shift ;;
  esac
done

PERIOD="$(date '+%Y-%m-01') ~ $(date '+%Y-%m-%d')"
TIMESTAMP="$(date '+%Y%m%d_%H%M')"

# ─── 쿼리: 임계값 이상인 조직 목록 ────────────────────────────────────────────
QUERY_ALERT="
SELECT
    o.name                                                          AS org_name,
    ROUND(SUM(ce.cost_cents) / 100.0, 2)                            AS cost_usd,
    ROUND(bp.monthly_limit_cents / 100.0, 2)                        AS limit_usd,
    ROUND(SUM(ce.cost_cents) * 100.0 / bp.monthly_limit_cents, 2)   AS usage_pct,
    ROUND((bp.monthly_limit_cents - SUM(ce.cost_cents)) / 100.0, 2) AS remaining_usd
FROM cost_events ce
JOIN organizations o ON ce.organization_id = o.id
JOIN budget_policies bp ON ce.organization_id = bp.organization_id
WHERE ce.created >= DATE_TRUNC('month', CURRENT_DATE)
  AND ce.created <  CURRENT_TIMESTAMP + INTERVAL '1 second'
  AND bp.monthly_limit_cents > 0
GROUP BY o.id, o.name, bp.monthly_limit_cents
HAVING ROUND(SUM(ce.cost_cents) * 100.0 / bp.monthly_limit_cents, 2) >= ${ALERT_THRESHOLD}
ORDER BY usage_pct DESC;
"

# ─── 전체 조직 현황 쿼리 (알림 메시지 하단 표) ────────────────────────────────
QUERY_ALL="
SELECT
    o.name                                                          AS \"조직명\",
    ROUND(SUM(ce.cost_cents) / 100.0, 2)                            AS \"비용(USD)\",
    ROUND(bp.monthly_limit_cents / 100.0, 2)                        AS \"한도(USD)\",
    CASE WHEN bp.monthly_limit_cents > 0
         THEN ROUND(SUM(ce.cost_cents) * 100.0 / bp.monthly_limit_cents, 2)
         ELSE NULL END                                              AS \"사용율(%)\",
    CASE WHEN bp.monthly_limit_cents > 0
         THEN ROUND((bp.monthly_limit_cents - SUM(ce.cost_cents)) / 100.0, 2)
         ELSE NULL END                                              AS \"잔여(USD)\"
FROM cost_events ce
JOIN organizations o ON ce.organization_id = o.id
LEFT JOIN budget_policies bp ON ce.organization_id = bp.organization_id
WHERE ce.created >= DATE_TRUNC('month', CURRENT_DATE)
  AND ce.created <  CURRENT_TIMESTAMP + INTERVAL '1 second'
GROUP BY o.id, o.name, bp.monthly_limit_cents
ORDER BY SUM(ce.cost_cents) DESC;
"

run_psql() {
  PGPASSWORD="$DB_PASSWORD" psql \
    -h "$DB_HOST" -p "$DB_PORT" -U "$DB_USER" -d "$DB_NAME" \
    --pset=border=2 --pset=format=aligned --pset=footer=off \
    -c "$1"
}

# 임계값 초과 조직 조회 (raw: tsv)
ALERT_RAW=$(PGPASSWORD="$DB_PASSWORD" psql \
  -h "$DB_HOST" -p "$DB_PORT" -U "$DB_USER" -d "$DB_NAME" \
  --no-align --tuples-only --field-separator=$'\t' \
  -c "$QUERY_ALERT" 2>/dev/null || true)

ALL_TABLE=$(run_psql "$QUERY_ALL" 2>/dev/null || true)

# ─── 알림 메시지 조립 ─────────────────────────────────────────────────────────
build_message() {
  local header body org cost limit pct remaining level icon lines

  header="⚠️  *예산 한도 임박 알림* — ${PERIOD}"
  header+=$'\n'"임계값: ${ALERT_THRESHOLD}% 이상인 조직이 감지되었습니다."
  header+=$'\n'

  body=""
  lines=0
  while IFS=$'\t' read -r org cost limit pct remaining; do
    [[ -z "$org" ]] && continue
    lines=$((lines + 1))

    if   awk "BEGIN{exit !($pct >= 100)}"; then icon="🔴"; level="*한도 초과*"
    elif awk "BEGIN{exit !($pct >= 95)}";  then icon="🔴"; level="*위험 (≥95%)*"
    elif awk "BEGIN{exit !($pct >= 90)}";  then icon="🟠"; level="경고 (≥90%)"
    else                                        icon="🟡"; level="주의"
    fi

    body+="${icon} *${org}*  ${level}"$'\n'
    body+="   • 사용: \$${cost} / \$${limit}  (${pct}%)"$'\n'
    body+="   • 잔여: \$${remaining}"$'\n\n'
  done <<< "$ALERT_RAW"

  if [[ $lines -eq 0 ]]; then
    echo "✅ 임계값(${ALERT_THRESHOLD}%) 초과 조직 없음 — 점검 완료 (${PERIOD})"
    return
  fi

  printf '%s\n%s\n```\n%s\n```' "$header" "$body" "$ALL_TABLE"
}

MESSAGE=$(build_message)

echo "$MESSAGE"
echo ""

# ─── 파일 저장 ────────────────────────────────────────────────────────────────
if [[ -n "$OUTPUT_DIR" ]]; then
  mkdir -p "$OUTPUT_DIR"
  OUTPUT_FILE="${OUTPUT_DIR}/alert_budget-${TIMESTAMP}.txt"
  echo "$MESSAGE" > "$OUTPUT_FILE"
  echo "[output] 알림 저장 완료: $OUTPUT_FILE"
fi

# ─── 웹훅 전송 ────────────────────────────────────────────────────────────────
if [[ -n "$WEBHOOK_URL" ]]; then
  # 임계값 초과 조직이 없으면 전송하지 않음
  if [[ -z "$(echo "$ALERT_RAW" | tr -d '[:space:]')" ]]; then
    echo "[webhook] 임계값 초과 조직 없음 — 전송 생략"
    exit 0
  fi

  PAYLOAD=$(printf '%s' "$MESSAGE" \
    | python3 -c 'import json,sys; print(json.dumps({"text": sys.stdin.read()}))')
  HTTP_STATUS=$(curl -s -o /dev/null -w "%{http_code}" \
    -X POST "$WEBHOOK_URL" \
    -H "Content-Type: application/json" \
    -d "$PAYLOAD")
  if [[ "$HTTP_STATUS" == "200" ]]; then
    echo "[webhook] Google Chat 전송 완료 (HTTP $HTTP_STATUS)"
  else
    echo "[webhook] 전송 실패 (HTTP $HTTP_STATUS)" >&2
    exit 1
  fi
fi
