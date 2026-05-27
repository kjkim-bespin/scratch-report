#!/usr/bin/env python3
"""
비용 리포트 MD 문서 생성 및 스토리지 업로드

사용법:
    python3 generate_cost_report_md.py [--month YYYY-MM] [--no-upload] [--output-dir DIR]

환경변수:
    DB_HOST             PostgreSQL 호스트 (기본값: localhost)
    DB_PORT             PostgreSQL 포트 (기본값: 5432)
    DB_USER             PostgreSQL 사용자 (기본값: postgres)
    DB_PASSWORD         PostgreSQL 비밀번호
    DB_NAME             데이터베이스 이름 (기본값: postgres)

    STORAGE_TYPE        스토리지 유형: s3 | minio (기본값: s3)
    STORAGE_BUCKET      업로드 대상 버킷 이름
    STORAGE_ENDPOINT    MinIO 또는 S3 호환 엔드포인트 URL (MinIO 사용 시 필수)
    AWS_REGION          AWS 리전 (기본값: ap-northeast-2)
    REPORT_EXPIRE_DAYS  다운로드 링크 유효기간 (기본값: 7일)
"""

import argparse
import csv
import io
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone, timedelta

# ─────────────────────────────────────────────────────────────────
# 설정
# ─────────────────────────────────────────────────────────────────
DB_HOST     = os.environ.get("DB_HOST", "localhost")
DB_PORT     = os.environ.get("DB_PORT", "5432")
DB_USER     = os.environ.get("DB_USER", "postgres")
DB_PASSWORD = os.environ.get("DB_PASSWORD", "")
DB_NAME     = os.environ.get("DB_NAME", "postgres")

STORAGE_TYPE     = os.environ.get("STORAGE_TYPE", "s3")
STORAGE_BUCKET   = os.environ.get("STORAGE_BUCKET", "")
STORAGE_ENDPOINT = os.environ.get("STORAGE_ENDPOINT", "")
AWS_REGION       = os.environ.get("AWS_REGION",
                   os.environ.get("AWS_DEFAULT_REGION", "ap-northeast-2"))
REPORT_EXPIRE_DAYS = int(os.environ.get("REPORT_EXPIRE_DAYS", "7"))

KST = timezone(timedelta(hours=9))
DB_URL = f"postgresql://{DB_USER}:{DB_PASSWORD}@{DB_HOST}:{DB_PORT}/{DB_NAME}"


# ─────────────────────────────────────────────────────────────────
# DB 유틸
# ─────────────────────────────────────────────────────────────────
def run_psql(query: str) -> list:
    """
    COPY (SELECT ...) TO STDOUT 방식으로 쿼리를 실행하여
    dict 리스트를 반환합니다. (psql 클라이언트 권한만 필요)
    """
    env = {**os.environ, "PGPASSWORD": DB_PASSWORD}
    copy_cmd = f"COPY ({query}) TO STDOUT WITH (FORMAT CSV, HEADER)"

    proc = subprocess.run(
        ["psql", DB_URL, "--no-psqlrc", "-c", copy_cmd],
        capture_output=True, text=True, env=env,
    )
    if proc.returncode != 0:
        print(f"[ERROR] psql 실행 실패:\n{proc.stderr}", file=sys.stderr)
        sys.exit(1)

    return list(csv.DictReader(io.StringIO(proc.stdout)))


# ─────────────────────────────────────────────────────────────────
# SQL 쿼리
# ─────────────────────────────────────────────────────────────────
def _month_range(year: int, month: int):
    """해당 연월의 시작일과 다음 달 시작일을 반환합니다."""
    start = f"{year:04d}-{month:02d}-01"
    next_year, next_month = (year + 1, 1) if month == 12 else (year, month + 1)
    end = f"{next_year:04d}-{next_month:02d}-01"
    return start, end


def q_current_month(year: int, month: int) -> str:
    start, end = _month_range(year, month)
    return f"""
SELECT
    o.id::text                                          AS org_id,
    o.name                                              AS org_name,
    COALESCE(p.id::text, 'common_' || o.id::text)      AS project_id,
    COALESCE(p.name, '프로젝트 공통')                    AS project_name,
    COUNT(*)::text                                      AS event_count,
    COALESCE(SUM(ce.input_tokens),       0)::text       AS input_tokens,
    COALESCE(SUM(ce.output_tokens),      0)::text       AS output_tokens,
    COALESCE(SUM(ce.cache_write_tokens), 0)::text       AS cache_write_tokens,
    COALESCE(SUM(ce.cache_read_tokens),  0)::text       AS cache_read_tokens,
    COALESCE(SUM(ce.cost_cents),         0)::text       AS cost_cents,
    COALESCE(bp.monthly_limit_cents,     0)::text       AS monthly_limit_cents
FROM cost_events ce
JOIN  organizations  o  ON ce.organization_id = o.id
LEFT JOIN projects   p  ON ce.project_id      = p.id
LEFT JOIN budget_policies bp ON ce.organization_id = bp.organization_id
WHERE ce.created >= '{start}'
  AND ce.created <  '{end}'
GROUP BY o.id, o.name, p.id, p.name, bp.monthly_limit_cents
ORDER BY o.name ASC, SUM(ce.cost_cents) DESC
"""


def q_prev_month(year: int, month: int) -> str:
    prev_year, prev_month = (year - 1, 12) if month == 1 else (year, month - 1)
    start, end = _month_range(prev_year, prev_month)
    return f"""
SELECT
    o.id::text                                          AS org_id,
    COALESCE(p.id::text, 'common_' || o.id::text)      AS project_id,
    COALESCE(SUM(ce.cost_cents), 0)::text               AS cost_cents
FROM cost_events ce
JOIN  organizations o ON ce.organization_id = o.id
LEFT JOIN projects  p ON ce.project_id      = p.id
WHERE ce.created >= '{start}'
  AND ce.created <  '{end}'
GROUP BY o.id, p.id
"""


def q_daily(year: int, month: int) -> str:
    start, end = _month_range(year, month)
    return f"""
SELECT
    DATE(ce.created AT TIME ZONE 'Asia/Seoul')::text    AS date,
    o.id::text                                          AS org_id,
    COALESCE(p.id::text, 'common_' || o.id::text)      AS project_id,
    COUNT(*)::text                                      AS event_count,
    COALESCE(SUM(ce.input_tokens),  0)::text            AS input_tokens,
    COALESCE(SUM(ce.output_tokens), 0)::text            AS output_tokens,
    COALESCE(SUM(ce.cost_cents),    0)::text            AS cost_cents
FROM cost_events ce
JOIN  organizations o ON ce.organization_id = o.id
LEFT JOIN projects  p ON ce.project_id      = p.id
WHERE ce.created >= '{start}'
  AND ce.created <  '{end}'
GROUP BY DATE(ce.created AT TIME ZONE 'Asia/Seoul'), o.id, p.id
ORDER BY DATE(ce.created AT TIME ZONE 'Asia/Seoul') ASC,
         SUM(ce.cost_cents) DESC
"""


# ─────────────────────────────────────────────────────────────────
# 포맷 유틸
# ─────────────────────────────────────────────────────────────────
def _cents(s) -> int:
    """문자열/숫자를 정수 cents로 변환합니다."""
    try:
        return int(float(s or 0))
    except (ValueError, TypeError):
        return 0


def fmt_usd(cents_val) -> str:
    usd = _cents(cents_val) / 100.0
    return f"${usd:,.4f}"


def fmt_num(val) -> str:
    try:
        return f"{int(float(val or 0)):,}"
    except (ValueError, TypeError):
        return str(val) if val else "—"


def mom_change(curr_cents: int, prev_cents: int) -> str:
    """전월 대비 증감률 문자열을 반환합니다."""
    if prev_cents == 0:
        return "신규" if curr_cents > 0 else "—"
    diff     = curr_cents - prev_cents
    pct      = diff / prev_cents * 100
    diff_usd = abs(diff) / 100.0
    if diff >= 0:
        return f"+${diff_usd:,.4f} (+{pct:.2f}%)"
    else:
        return f"-${diff_usd:,.4f} ({pct:.2f}%)"


def md_table(headers: list, rows: list) -> str:
    """Markdown 테이블 문자열을 생성합니다."""
    if not rows:
        return "_데이터 없음_"

    # 컬럼 최소 너비: 헤더 길이 기준 (한국어 폭 고려 없이 바이트 기준)
    col_w = [len(str(h)) for h in headers]
    for row in rows:
        for i, cell in enumerate(row):
            if i < len(col_w):
                col_w[i] = max(col_w[i], len(str(cell)))

    def pad(s, w):
        return str(s).ljust(w)

    hdr = "| " + " | ".join(pad(h, col_w[i]) for i, h in enumerate(headers)) + " |"
    sep = "| " + " | ".join("-" * col_w[i] for i in range(len(headers))) + " |"
    body = [
        "| " + " | ".join(pad(str(c), col_w[i]) for i, c in enumerate(row)) + " |"
        for row in rows
    ]
    return "\n".join([hdr, sep] + body)


# ─────────────────────────────────────────────────────────────────
# MD 리포트 빌더
# ─────────────────────────────────────────────────────────────────
def build_project_report(
    row: dict,
    prev_cents: int,
    daily_rows: list,
    report_month: str,
    generated_at: str,
) -> str:
    """단일 프로젝트의 월간 비용 리포트 MD를 생성합니다."""
    curr_cents  = _cents(row["cost_cents"])
    limit_cents = _cents(row["monthly_limit_cents"])

    diff_cents = curr_cents - prev_cents
    diff_usd   = diff_cents / 100.0
    diff_sign  = "+" if diff_cents >= 0 else ""

    usage_rate = (
        f"{curr_cents / limit_cents * 100:.2f}%"
        if limit_cents > 0 else "—"
    )

    year_str, mon_str = report_month.split("-")
    period_start = f"{year_str}-{mon_str}-01"
    today_str = datetime.now(KST).strftime("%Y-%m-%d")

    # ── 요약 테이블 ──────────────────────────────────
    summary_rows = [
        ["조직명",         row["org_name"]],
        ["프로젝트명",     row["project_name"]],
        ["집계 기간",      f"{period_start} ~ {today_str}"],
        ["이달 비용",      fmt_usd(row["cost_cents"])],
        ["전월 비용",      fmt_usd(prev_cents)],
        ["전월 대비",      mom_change(curr_cents, prev_cents)],
        ["조직 예산 한도", fmt_usd(row["monthly_limit_cents"])],
        ["조직 예산 사용율", usage_rate],
    ]

    # ── 토큰 유형별 상세 내역 ────────────────────────
    # (비용 항목별 상세: 서비스 유형 = 토큰 유형)
    token_rows = [
        ["Input 토큰 (프롬프트)",  fmt_num(row["input_tokens"])],
        ["Output 토큰 (응답)",     fmt_num(row["output_tokens"])],
        ["Cache Write 토큰",       fmt_num(row["cache_write_tokens"])],
        ["Cache Read 토큰",        fmt_num(row["cache_read_tokens"])],
        ["총 이벤트 수",           fmt_num(row["event_count"])],
        ["합산 비용",              fmt_usd(row["cost_cents"])],
    ]

    # ── 일별 내역 ────────────────────────────────────
    cumulative = 0
    daily_table_rows = []
    for dr in daily_rows:
        day_cents  = _cents(dr["cost_cents"])
        cumulative += day_cents
        daily_table_rows.append([
            dr["date"],
            fmt_num(dr["event_count"]),
            fmt_num(dr["input_tokens"]),
            fmt_num(dr["output_tokens"]),
            fmt_usd(dr["cost_cents"]),
            fmt_usd(cumulative),
        ])

    daily_section = ""
    if daily_table_rows:
        daily_section = f"""
## 📅 일별 비용 추이

{md_table(
    ["날짜", "이벤트 수", "Input 토큰", "Output 토큰", "일별 비용 (USD)", "누적 비용 (USD)"],
    daily_table_rows,
)}
"""

    return f"""# {row["org_name"]} / {row["project_name"]} — 비용 리포트 ({report_month})

> **집계 기간**: {period_start} ~ {today_str}  
> **생성 일시**: {generated_at}

---

## 📊 요약

{md_table(["항목", "값"], summary_rows)}

---

## 🔢 비용 항목별 상세 내역 (서비스: LLM API)

{md_table(["항목 (리소스 유형)", "수량 / 금액"], token_rows)}

> 비용은 각 토큰 유형의 사용량에 모델별 단가를 적용한 합산값입니다.

---
{daily_section}
---

*리포트 생성: scratch-report | {generated_at}*
"""


def build_summary_report(
    current_rows: list,
    prev_map: dict,
    report_month: str,
    generated_at: str,
) -> str:
    """전체 프로젝트 요약 리포트 MD를 생성합니다."""
    year_str, mon_str = report_month.split("-")
    period_start = f"{year_str}-{mon_str}-01"
    today_str = datetime.now(KST).strftime("%Y-%m-%d")

    total_curr = sum(_cents(r["cost_cents"]) for r in current_rows)
    total_prev = sum(prev_map.values())

    diff_cents = total_curr - total_prev
    diff_usd   = diff_cents / 100.0
    diff_sign  = "+" if diff_cents >= 0 else ""

    top_summary_rows = [
        ["집계 기간",         f"{period_start} ~ {today_str}"],
        ["이달 총 비용 (USD)", fmt_usd(total_curr)],
        ["전월 총 비용 (USD)", fmt_usd(total_prev)],
        ["전월 대비",          mom_change(total_curr, total_prev)],
        ["총 이벤트 수",       fmt_num(sum(_cents(r["event_count"]) for r in current_rows))],
    ]

    # 프로젝트별 요약 테이블
    proj_rows = []
    for r in current_rows:
        pid        = r["project_id"]
        curr_c     = _cents(r["cost_cents"])
        prev_c     = prev_map.get(pid, 0)
        limit_c    = _cents(r["monthly_limit_cents"])
        usage_rate = f"{curr_c / limit_c * 100:.2f}%" if limit_c > 0 else "—"

        proj_rows.append([
            r["org_name"],
            r["project_name"],
            fmt_num(r["event_count"]),
            fmt_usd(r["cost_cents"]),
            fmt_usd(prev_c),
            mom_change(curr_c, prev_c),
            fmt_usd(r["monthly_limit_cents"]),
            usage_rate,
        ])

    return f"""# 전체 비용 요약 리포트 ({report_month})

> **집계 기간**: {period_start} ~ {today_str}  
> **생성 일시**: {generated_at}

---

## 📊 전체 요약

{md_table(["항목", "값"], top_summary_rows)}

---

## 📁 프로젝트별 비용 현황

{md_table(
    ["조직명", "프로젝트명", "이벤트 수",
     "이달 비용 (USD)", "전월 비용 (USD)", "전월 대비",
     "예산 한도 (USD)", "예산 사용율"],
    proj_rows,
)}

---

*리포트 생성: scratch-report | {generated_at}*
"""


# ─────────────────────────────────────────────────────────────────
# 스토리지 업로드 & Presigned URL
# ─────────────────────────────────────────────────────────────────
def _aws_extra_args() -> list:
    """MinIO / S3 호환 엔드포인트 사용 시 추가 인수를 반환합니다."""
    if STORAGE_ENDPOINT:
        return ["--endpoint-url", STORAGE_ENDPOINT]
    return []


def upload_to_storage(local_path: str, s3_key: str) -> bool:
    """파일을 S3/MinIO 버킷에 업로드합니다."""
    s3_uri = f"s3://{STORAGE_BUCKET}/{s3_key}"
    cmd = (
        ["aws", "s3", "cp", local_path, s3_uri,
         "--region", AWS_REGION]
        + _aws_extra_args()
    )
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        print(f"[ERROR] 업로드 실패 ({s3_uri}):\n{proc.stderr}", file=sys.stderr)
        return False
    print(f"[OK] 업로드 완료: {s3_uri}")
    return True


def generate_presigned_url(s3_key: str) -> str:
    """S3/MinIO 객체에 대한 Presigned 다운로드 URL을 생성합니다."""
    s3_uri          = f"s3://{STORAGE_BUCKET}/{s3_key}"
    expire_seconds  = REPORT_EXPIRE_DAYS * 86400

    cmd = (
        ["aws", "s3", "presign", s3_uri,
         "--expires-in", str(expire_seconds),
         "--region", AWS_REGION]
        + _aws_extra_args()
    )
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        print(f"[ERROR] Presigned URL 생성 실패:\n{proc.stderr}", file=sys.stderr)
        return ""

    url = proc.stdout.strip()
    print(f"[OK] 다운로드 링크 (유효기간 {REPORT_EXPIRE_DAYS}일): {url}")
    return url


def upload_and_presign(local_path: str, s3_key: str) -> str:
    """업로드 후 Presigned URL을 반환하는 통합 함수입니다."""
    if not STORAGE_BUCKET:
        print("[WARN] STORAGE_BUCKET 이 설정되지 않아 업로드를 건너뜁니다.", file=sys.stderr)
        return ""
    if not upload_to_storage(local_path, s3_key):
        return ""
    return generate_presigned_url(s3_key)


# ─────────────────────────────────────────────────────────────────
# 메인
# ─────────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(
        description="비용 리포트 MD 문서 생성 및 스토리지 업로드"
    )
    parser.add_argument(
        "--month", default=None,
        help="대상 연월 (YYYY-MM). 기본값: 이번 달",
    )
    parser.add_argument(
        "--no-upload", action="store_true",
        help="스토리지 업로드 없이 로컬 파일만 생성합니다",
    )
    parser.add_argument(
        "--output-dir", default=".",
        help="로컬 출력 루트 디렉터리 (기본: 현재 디렉터리)",
    )
    args = parser.parse_args()

    now = datetime.now(KST)
    if args.month:
        try:
            year, month = map(int, args.month.split("-"))
        except ValueError:
            print("[ERROR] --month 형식이 잘못되었습니다. YYYY-MM 형식으로 입력하세요.", file=sys.stderr)
            sys.exit(1)
    else:
        year, month = now.year, now.month

    report_month = f"{year:04d}-{month:02d}"
    generated_at = now.strftime("%Y-%m-%d %H:%M:%S KST")

    print(f"[INFO] 대상 기간    : {report_month}")
    print(f"[INFO] DB 연결      : {DB_HOST}:{DB_PORT}/{DB_NAME}")
    if not args.no_upload:
        print(f"[INFO] 스토리지     : {STORAGE_TYPE} / 버킷: {STORAGE_BUCKET or '(미설정)'}")
        print(f"[INFO] 링크 유효기간: {REPORT_EXPIRE_DAYS}일")

    # ── DB 조회 ──────────────────────────────────────────────────
    print("\n[INFO] 현재 달 데이터 조회 중 ...")
    current_rows = run_psql(q_current_month(year, month))

    print("[INFO] 전월 데이터 조회 중 ...")
    prev_rows = run_psql(q_prev_month(year, month))
    prev_map  = {r["project_id"]: _cents(r["cost_cents"]) for r in prev_rows}

    print("[INFO] 일별 데이터 조회 중 ...")
    daily_rows = run_psql(q_daily(year, month))
    daily_by_project: dict = {}
    for dr in daily_rows:
        daily_by_project.setdefault(dr["project_id"], []).append(dr)

    if not current_rows:
        print(f"[WARN] {report_month} 에 해당하는 비용 데이터가 없습니다.", file=sys.stderr)

    os.makedirs(args.output_dir, exist_ok=True)
    results = []

    # ── 프로젝트별 리포트 생성 ───────────────────────────────────
    for row in current_rows:
        pid        = row["project_id"]
        prev_cents = prev_map.get(pid, 0)
        proj_daily = daily_by_project.get(pid, [])

        md_content = build_project_report(
            row=row,
            prev_cents=prev_cents,
            daily_rows=proj_daily,
            report_month=report_month,
            generated_at=generated_at,
        )

        # 파일명에 사용할 수 있는 안전한 project_id
        safe_pid = re.sub(r"[^a-zA-Z0-9_\-]", "_", pid)
        local_dir  = os.path.join(args.output_dir, "reports", safe_pid, report_month)
        os.makedirs(local_dir, exist_ok=True)
        local_path = os.path.join(local_dir, "cost_report.md")

        with open(local_path, "w", encoding="utf-8") as fh:
            fh.write(md_content)
        print(f"[INFO] 로컬 저장: {local_path}")

        # 스토리지 업로드 (파일 경로 규칙: reports/{project_id}/{YYYY-MM}/cost_report.md)
        s3_key = f"reports/{pid}/{report_month}/cost_report.md"
        url = ""
        if not args.no_upload:
            url = upload_and_presign(local_path, s3_key)

        results.append({
            "project_id":    pid,
            "project_name":  row["project_name"],
            "org_name":      row["org_name"],
            "report_month":  report_month,
            "local_path":    local_path,
            "s3_key":        s3_key,
            "download_url":  url,
            "expires_days":  REPORT_EXPIRE_DAYS if url else None,
        })

    # ── 전체 요약 리포트 생성 ────────────────────────────────────
    summary_md = build_summary_report(current_rows, prev_map, report_month, generated_at)

    summary_local_dir  = os.path.join(args.output_dir, "reports", "summary", report_month)
    os.makedirs(summary_local_dir, exist_ok=True)
    summary_local_path = os.path.join(summary_local_dir, "cost_report.md")

    with open(summary_local_path, "w", encoding="utf-8") as fh:
        fh.write(summary_md)
    print(f"[INFO] 요약 리포트 로컬 저장: {summary_local_path}")

    summary_s3_key = f"reports/summary/{report_month}/cost_report.md"
    summary_url = ""
    if not args.no_upload:
        summary_url = upload_and_presign(summary_local_path, summary_s3_key)

    results.append({
        "project_id":   "summary",
        "project_name": "전체 요약",
        "org_name":     "ALL",
        "report_month": report_month,
        "local_path":   summary_local_path,
        "s3_key":       summary_s3_key,
        "download_url": summary_url,
        "expires_days": REPORT_EXPIRE_DAYS if summary_url else None,
    })

    # ── 결과 출력 ────────────────────────────────────────────────
    print("\n" + "=" * 64)
    print(f"  리포트 생성 완료 ({report_month})")
    print("=" * 64)
    for r in results:
        label = f"{r['org_name']} / {r['project_name']}"
        print(f"\n  📄 {label}")
        print(f"     로컬 파일  : {r['local_path']}")
        if r["download_url"]:
            print(f"     다운로드 URL (유효기간 {r['expires_days']}일):")
            print(f"     {r['download_url']}")
        else:
            print(f"     스토리지 업로드: 건너뜀 (--no-upload 또는 STORAGE_BUCKET 미설정)")

    # JSON 결과 파일 저장 (파이프라인 연동용)
    links_path = os.path.join(args.output_dir, f"report_links_{report_month}.json")
    with open(links_path, "w", encoding="utf-8") as fh:
        json.dump(results, fh, ensure_ascii=False, indent=2)
    print(f"\n  💾 링크 목록 JSON: {links_path}")
    print("=" * 64)


if __name__ == "__main__":
    main()
