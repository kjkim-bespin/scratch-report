#!/usr/bin/env bash
# 프로젝트별 이달 LLM 비용 집계 리포트
# 사용법: DB_HOST=... DB_USER=... DB_PASSWORD=... DB_NAME=... ./report_project_cost.sh [--detail|-d] [--webhook|-w WEBHOOK_URL]
#         WEBHOOK_URL 환경변수로도 지정 가능

DB_HOST="${DB_HOST:-localhost}"
DB_PORT="${DB_PORT:-5432}"
DB_USER="${DB_USER:-postgres}"
DB_PASSWORD="${DB_PASSWORD:-}"
DB_NAME="${DB_NAME:-postgres}"
WEBHOOK_URL="${WEBHOOK_URL:-}"

DB_URL="postgresql://${DB_USER}:${DB_PASSWORD}@${DB_HOST}:${DB_PORT}/${DB_NAME}"
DETAIL=false

while [[ $# -gt 0 ]]; do
  case "$1" in
    --detail|-d) DETAIL=true; shift ;;
    --webhook|-w) WEBHOOK_URL="$2"; shift 2 ;;
    *) shift ;;
  esac
done

if $DETAIL; then
  QUERY="
SELECT
    o.name                                        AS \"조직명\",
    COALESCE(p.name, '(프로젝트 없음)')            AS \"프로젝트명\",
    COUNT(*)                                      AS \"이벤트 수\",
    SUM(ce.input_tokens)                          AS \"Input 토큰\",
    SUM(ce.output_tokens)                         AS \"Output 토큰\",
    SUM(ce.cache_write_tokens)                    AS \"Cache Write\",
    SUM(ce.cache_read_tokens)                     AS \"Cache Read\",
    SUM(ce.cost_cents)                            AS \"비용(cents)\",
    ROUND(SUM(ce.cost_cents) / 100.0, 4)          AS \"비용(USD)\"
FROM cost_events ce
JOIN organizations o ON ce.organization_id = o.id
LEFT JOIN projects p ON ce.project_id = p.id
WHERE ce.created >= DATE_TRUNC('month', CURRENT_DATE)
  AND ce.created <  CURRENT_TIMESTAMP + INTERVAL '1 second'
GROUP BY o.id, o.name, p.id, p.name
ORDER BY o.name ASC, SUM(ce.cost_cents) DESC;
"
  QUERY_DAILY="
SELECT
    DATE(ce.created AT TIME ZONE 'Asia/Seoul') AS \"날짜\",
    o.name                                        AS \"조직명\",
    COALESCE(p.name, '(프로젝트 없음)')            AS \"프로젝트명\",
    COUNT(*)                                      AS \"이벤트 수\",
    SUM(ce.input_tokens)                          AS \"Input 토큰\",
    SUM(ce.output_tokens)                         AS \"Output 토큰\",
    SUM(ce.cache_write_tokens)                    AS \"Cache Write\",
    SUM(ce.cache_read_tokens)                     AS \"Cache Read\",
    SUM(ce.cost_cents)                            AS \"비용(cents)\",
    ROUND(SUM(ce.cost_cents) / 100.0, 4)          AS \"비용(USD)\"
FROM cost_events ce
JOIN organizations o ON ce.organization_id = o.id
LEFT JOIN projects p ON ce.project_id = p.id
WHERE ce.created >= DATE_TRUNC('month', CURRENT_DATE)
  AND ce.created <  CURRENT_TIMESTAMP + INTERVAL '1 second'
GROUP BY DATE(ce.created AT TIME ZONE 'Asia/Seoul'), o.id, o.name, p.id, p.name
ORDER BY DATE(ce.created AT TIME ZONE 'Asia/Seoul') ASC, o.name ASC, SUM(ce.cost_cents) DESC;
"
else
  QUERY="
SELECT
    o.name                                        AS \"조직명\",
    COALESCE(p.name, '(프로젝트 없음)')            AS \"프로젝트명\",
    COUNT(*)                                      AS \"이벤트 수\",
    ROUND(SUM(ce.cost_cents) / 100.0, 4)          AS \"비용(USD)\"
FROM cost_events ce
JOIN organizations o ON ce.organization_id = o.id
LEFT JOIN projects p ON ce.project_id = p.id
WHERE ce.created >= DATE_TRUNC('month', CURRENT_DATE)
  AND ce.created <  CURRENT_TIMESTAMP + INTERVAL '1 second'
GROUP BY o.id, o.name, p.id, p.name
ORDER BY o.name ASC, SUM(ce.cost_cents) DESC;
"
fi

PERIOD="$(date '+%Y-%m-01') ~ $(date '+%Y-%m-%d')"
TITLE="=== 프로젝트별 이달 비용 집계 ($PERIOD)${DETAIL:+ [상세]} ==="
TITLE_DAILY="=== 프로젝트별 날짜별 비용 집계 ($PERIOD) ==="

run_psql() {
  psql "$DB_URL" --pset=border=2 --pset=format=aligned --pset=footer=off -c "$1"
}

RESULT=$(run_psql "$QUERY")

echo "$TITLE"
echo ""
echo "$RESULT"

if $DETAIL; then
  RESULT_DAILY=$(run_psql "$QUERY_DAILY")
  echo ""
  echo "$TITLE_DAILY"
  echo ""
  echo "$RESULT_DAILY"
fi

if [[ -n "$WEBHOOK_URL" ]]; then
  if $DETAIL; then
    FULL_OUTPUT=$(printf '%s\n\n```\n%s\n```\n\n%s\n\n```\n%s\n```' \
      "$TITLE" "$RESULT" "$TITLE_DAILY" "$RESULT_DAILY")
  else
    FULL_OUTPUT=$(printf '%s\n\n```\n%s\n```' "$TITLE" "$RESULT")
  fi

  PAYLOAD=$(printf '%s' "$FULL_OUTPUT" \
    | python3 -c 'import json,sys; print(json.dumps({"text": sys.stdin.read()}))')
  HTTP_STATUS=$(curl -s -o /dev/null -w "%{http_code}" \
    -X POST "$WEBHOOK_URL" \
    -H "Content-Type: application/json" \
    -d "$PAYLOAD")
  if [[ "$HTTP_STATUS" == "200" ]]; then
    echo ""
    echo "[webhook] Google Chat 전송 완료 (HTTP $HTTP_STATUS)"
  else
    echo ""
    echo "[webhook] 전송 실패 (HTTP $HTTP_STATUS)" >&2
    exit 1
  fi
fi
