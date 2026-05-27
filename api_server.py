#!/usr/bin/env python3
"""
Cost Report REST API Server

FastAPI 기반 REST API 서버.
generate_cost_report_md.py 의 핵심 로직을 HTTP 엔드포인트로 노출합니다.

엔드포인트:
    POST /api/reports/generate   비용 리포트 생성 & 스토리지 업로드
    GET  /api/reports            기존 리포트의 다운로드 링크 재발급

사용법:
    uvicorn api_server:app --host 0.0.0.0 --port 8000

환경변수:
    (generate_cost_report_md.py 와 동일: DB_*, STORAGE_*, AWS_*, REPORT_EXPIRE_DAYS)
"""

import csv
import io
import logging
import os
import re
import subprocess
import tempfile
from datetime import datetime, timezone, timedelta
from typing import Optional

import boto3
import botocore.exceptions
from fastapi import FastAPI, HTTPException, Query, BackgroundTasks
from fastapi.responses import JSONResponse
from pydantic import BaseModel, model_validator

# ── generate_cost_report_md 에서 "순수 함수" 만 임포트 ──────────────
# sys.exit() 를 호출하는 run_psql / upload_* 는 임포트하지 않습니다.
from generate_cost_report_md import (
    _cents,
    fmt_usd,
    fmt_num,
    mom_change,
    md_table,
    build_project_report,
    build_summary_report,
    q_current_month,
    q_prev_month,
    q_daily,
    KST,
    # 모듈 레벨 설정값 (서버 기동 시 환경변수로 고정)
    DB_HOST,
    DB_PORT,
    DB_USER,
    DB_PASSWORD,
    DB_NAME,
    STORAGE_BUCKET,
    STORAGE_ENDPOINT,
    AWS_REGION,
    REPORT_EXPIRE_DAYS,
)

# ─────────────────────────────────────────────────────────────────
# 설정
# ─────────────────────────────────────────────────────────────────
DB_URL = f"postgresql://{DB_USER}:{DB_PASSWORD}@{DB_HOST}:{DB_PORT}/{DB_NAME}"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("cost_report_api")

app = FastAPI(
    title="Cost Report API",
    version="1.0.0",
    description="월별·프로젝트별 LLM 비용 리포트 생성 및 다운로드 링크 제공 API",
)


# ─────────────────────────────────────────────────────────────────
# Pydantic 모델
# ─────────────────────────────────────────────────────────────────
class GenerateReportRequest(BaseModel):
    project_id: Optional[str] = None
    """특정 프로젝트 ID. null 이면 전체 프로젝트 + 요약 리포트 생성."""

    month: Optional[str] = None
    """대상 연월 (YYYY-MM). 미입력 시 이번 달."""

    @model_validator(mode="after")
    def validate_month_format(self) -> "GenerateReportRequest":
        if self.month is not None:
            try:
                year, mon = map(int, self.month.split("-"))
                if not (1 <= mon <= 12):
                    raise ValueError
            except (ValueError, AttributeError):
                raise ValueError("month 는 YYYY-MM 형식이어야 합니다 (예: 2026-05)")
        return self


class ReportResponse(BaseModel):
    report_url: str
    """Presigned 다운로드 URL (REPORT_EXPIRE_DAYS 일 유효)"""

    file_path: str
    """스토리지 내 파일 경로 (S3 key)"""

    generated_at: str
    """리포트 생성 일시 (ISO 8601, UTC)"""

    expires_at: str
    """다운로드 링크 만료 일시 (ISO 8601, UTC)"""


class AllReportsResponse(BaseModel):
    summary: ReportResponse
    projects: list[ReportResponse]
    """project_id 가 null 일 때 생성된 각 프로젝트별 리포트 목록"""


# ─────────────────────────────────────────────────────────────────
# DB 헬퍼 (sys.exit 없는 안전한 버전)
# ─────────────────────────────────────────────────────────────────
def _safe_run_psql(query: str) -> list[dict]:
    """
    psql COPY … TO STDOUT CSV 방식으로 쿼리를 실행하여 dict 리스트를 반환합니다.
    실패 시 HTTPException 을 발생시킵니다 (서버 프로세스를 종료하지 않음).
    """
    env = {**os.environ, "PGPASSWORD": DB_PASSWORD}
    copy_cmd = f"COPY ({query}) TO STDOUT WITH (FORMAT CSV, HEADER)"

    try:
        proc = subprocess.run(
            ["psql", DB_URL, "--no-psqlrc", "-c", copy_cmd],
            capture_output=True,
            text=True,
            env=env,
            timeout=60,
        )
    except subprocess.TimeoutExpired:
        logger.error("psql 쿼리 타임아웃 (60초)")
        raise HTTPException(status_code=503, detail="DB 쿼리 타임아웃 (60초)")
    except FileNotFoundError:
        logger.error("psql 실행 파일을 찾을 수 없습니다")
        raise HTTPException(status_code=503, detail="psql 클라이언트를 찾을 수 없습니다")

    if proc.returncode != 0:
        err = proc.stderr.strip()
        logger.error(f"psql 실행 실패 (returncode={proc.returncode}): {err}")
        # 연결 오류와 SQL 오류를 구분
        if "could not connect" in err or "Connection refused" in err:
            raise HTTPException(status_code=503, detail=f"DB 연결 실패: {err[:300]}")
        raise HTTPException(status_code=503, detail=f"DB 쿼리 실패: {err[:300]}")

    return list(csv.DictReader(io.StringIO(proc.stdout)))


# ─────────────────────────────────────────────────────────────────
# S3/MinIO 헬퍼 (boto3 직접 사용 — aws CLI 불필요)
# ─────────────────────────────────────────────────────────────────
def _get_s3_client():
    """boto3 S3 클라이언트를 생성합니다."""
    kwargs: dict = {"region_name": AWS_REGION}
    if STORAGE_ENDPOINT:
        kwargs["endpoint_url"] = STORAGE_ENDPOINT
    return boto3.client("s3", **kwargs)


def _require_bucket() -> None:
    if not STORAGE_BUCKET:
        raise HTTPException(
            status_code=500,
            detail="STORAGE_BUCKET 환경변수가 설정되지 않았습니다",
        )


def _upload_to_s3(local_path: str, s3_key: str) -> None:
    """파일을 S3/MinIO 버킷에 업로드합니다. 실패 시 HTTPException."""
    _require_bucket()
    client = _get_s3_client()
    try:
        client.upload_file(local_path, STORAGE_BUCKET, s3_key)
        logger.info(f"S3 업로드 완료: s3://{STORAGE_BUCKET}/{s3_key}")
    except botocore.exceptions.ClientError as e:
        logger.error(f"S3 업로드 실패: {e}")
        raise HTTPException(status_code=502, detail=f"스토리지 업로드 실패: {e}")
    except botocore.exceptions.BotoCoreError as e:
        logger.error(f"S3 업로드 오류 (BotoCore): {e}")
        raise HTTPException(status_code=502, detail=f"스토리지 오류: {e}")


def _presign(s3_key: str, now: datetime) -> tuple[str, str]:
    """
    Presigned GET URL 을 생성합니다.

    Returns:
        (url, expires_at_iso) 튜플
    """
    _require_bucket()
    client = _get_s3_client()
    expire_seconds = REPORT_EXPIRE_DAYS * 86400
    try:
        url = client.generate_presigned_url(
            "get_object",
            Params={"Bucket": STORAGE_BUCKET, "Key": s3_key},
            ExpiresIn=expire_seconds,
        )
    except botocore.exceptions.ClientError as e:
        logger.error(f"Presigned URL 생성 실패: {e}")
        raise HTTPException(status_code=502, detail=f"다운로드 링크 생성 실패: {e}")

    expires_at = (now + timedelta(seconds=expire_seconds)).strftime("%Y-%m-%dT%H:%M:%SZ")
    return url, expires_at


def _s3_object_exists(s3_key: str) -> bool:
    """S3 객체 존재 여부를 확인합니다."""
    _require_bucket()
    client = _get_s3_client()
    try:
        client.head_object(Bucket=STORAGE_BUCKET, Key=s3_key)
        return True
    except botocore.exceptions.ClientError as e:
        if e.response["Error"]["Code"] in ("404", "NoSuchKey"):
            return False
        logger.error(f"S3 head_object 오류: {e}")
        raise HTTPException(status_code=502, detail=f"스토리지 조회 실패: {e}")


# ─────────────────────────────────────────────────────────────────
# 리포트 생성 핵심 로직
# ─────────────────────────────────────────────────────────────────
def _parse_month(month: Optional[str]) -> tuple[int, int]:
    """YYYY-MM 파싱. None 이면 현재 월 반환."""
    if month is None:
        now = datetime.now(KST)
        return now.year, now.month
    year, mon = map(int, month.split("-"))
    return year, mon


def _fetch_db_data(year: int, month: int) -> tuple[list, dict, dict]:
    """
    DB에서 현재달·전월·일별 데이터를 일괄 조회합니다.

    Returns:
        (current_rows, prev_map, daily_by_project)
    """
    logger.info(f"DB 조회 시작: {year:04d}-{month:02d}")
    current_rows = _safe_run_psql(q_current_month(year, month))
    prev_rows     = _safe_run_psql(q_prev_month(year, month))
    daily_rows    = _safe_run_psql(q_daily(year, month))

    prev_map: dict = {r["project_id"]: _cents(r["cost_cents"]) for r in prev_rows}
    daily_by_project: dict = {}
    for dr in daily_rows:
        daily_by_project.setdefault(dr["project_id"], []).append(dr)

    logger.info(f"DB 조회 완료: 프로젝트 {len(current_rows)}개")
    return current_rows, prev_map, daily_by_project


def _write_and_upload_project(
    row: dict,
    prev_map: dict,
    daily_by_project: dict,
    report_month: str,
    generated_at_str: str,
    output_dir: str,
    now: datetime,
) -> ReportResponse:
    """단일 프로젝트 리포트를 생성·업로드하고 ReportResponse 를 반환합니다."""
    pid = row["project_id"]

    md_content = build_project_report(
        row=row,
        prev_cents=prev_map.get(pid, 0),
        daily_rows=daily_by_project.get(pid, []),
        report_month=report_month,
        generated_at=generated_at_str,
    )

    safe_pid   = re.sub(r"[^a-zA-Z0-9_\-]", "_", pid)
    local_dir  = os.path.join(output_dir, "reports", safe_pid, report_month)
    os.makedirs(local_dir, exist_ok=True)
    local_path = os.path.join(local_dir, "cost_report.md")

    with open(local_path, "w", encoding="utf-8") as fh:
        fh.write(md_content)

    s3_key = f"reports/{pid}/{report_month}/cost_report.md"
    _upload_to_s3(local_path, s3_key)
    url, expires_at = _presign(s3_key, now)

    return ReportResponse(
        report_url=url,
        file_path=s3_key,
        generated_at=now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        expires_at=expires_at,
    )


def _write_and_upload_summary(
    current_rows: list,
    prev_map: dict,
    report_month: str,
    generated_at_str: str,
    output_dir: str,
    now: datetime,
) -> ReportResponse:
    """전체 요약 리포트를 생성·업로드하고 ReportResponse 를 반환합니다."""
    md_content = build_summary_report(
        current_rows=current_rows,
        prev_map=prev_map,
        report_month=report_month,
        generated_at=generated_at_str,
    )

    local_dir  = os.path.join(output_dir, "reports", "summary", report_month)
    os.makedirs(local_dir, exist_ok=True)
    local_path = os.path.join(local_dir, "cost_report.md")

    with open(local_path, "w", encoding="utf-8") as fh:
        fh.write(md_content)

    s3_key = f"reports/summary/{report_month}/cost_report.md"
    _upload_to_s3(local_path, s3_key)
    url, expires_at = _presign(s3_key, now)

    return ReportResponse(
        report_url=url,
        file_path=s3_key,
        generated_at=now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        expires_at=expires_at,
    )


# ─────────────────────────────────────────────────────────────────
# 라우터
# ─────────────────────────────────────────────────────────────────
@app.post(
    "/api/reports/generate",
    response_model=ReportResponse,
    responses={
        200: {"description": "리포트 생성 완료"},
        207: {
            "description": "전체 프로젝트 리포트 생성 완료 (project_id=null)",
            "model": AllReportsResponse,
        },
        404: {"description": "project_id 를 찾을 수 없음"},
        422: {"description": "요청 파라미터 형식 오류"},
        502: {"description": "스토리지 업로드/링크 생성 실패"},
        503: {"description": "DB 연결 실패"},
    },
    summary="비용 리포트 생성",
    description=(
        "지정한 프로젝트·연월의 비용 리포트를 생성하고 스토리지에 업로드한 뒤 "
        "Presigned 다운로드 URL을 반환합니다.\n\n"
        "- `project_id` 가 `null` 이면 전체 프로젝트 + 요약 리포트를 모두 생성합니다.\n"
        "- `month` 미입력 시 현재 달을 기준으로 집계합니다."
    ),
)
async def generate_report(body: GenerateReportRequest):
    now           = datetime.now(KST)
    year, month   = _parse_month(body.month)
    report_month  = f"{year:04d}-{month:02d}"
    generated_at  = now.strftime("%Y-%m-%d %H:%M:%S KST")

    logger.info(
        f"POST /api/reports/generate  project_id={body.project_id!r}  month={report_month}"
    )

    current_rows, prev_map, daily_by_project = _fetch_db_data(year, month)

    # ── 특정 프로젝트 ────────────────────────────────────────────
    if body.project_id is not None:
        target = [r for r in current_rows if r["project_id"] == body.project_id]
        if not target:
            raise HTTPException(
                status_code=404,
                detail=f"project_id '{body.project_id}' 에 해당하는 데이터가 없습니다 ({report_month})",
            )

        with tempfile.TemporaryDirectory() as tmpdir:
            result = _write_and_upload_project(
                row=target[0],
                prev_map=prev_map,
                daily_by_project=daily_by_project,
                report_month=report_month,
                generated_at_str=generated_at,
                output_dir=tmpdir,
                now=now,
            )
        logger.info(f"리포트 생성 완료: {result.file_path}")
        return result

    # ── 전체 프로젝트 + 요약 (project_id=null) ──────────────────
    if not current_rows:
        raise HTTPException(
            status_code=404,
            detail=f"{report_month} 에 해당하는 비용 데이터가 없습니다",
        )

    with tempfile.TemporaryDirectory() as tmpdir:
        proj_results: list[ReportResponse] = [
            _write_and_upload_project(
                row=row,
                prev_map=prev_map,
                daily_by_project=daily_by_project,
                report_month=report_month,
                generated_at_str=generated_at,
                output_dir=tmpdir,
                now=now,
            )
            for row in current_rows
        ]

        summary_result = _write_and_upload_summary(
            current_rows=current_rows,
            prev_map=prev_map,
            report_month=report_month,
            generated_at_str=generated_at,
            output_dir=tmpdir,
            now=now,
        )

    logger.info(
        f"전체 리포트 생성 완료: 프로젝트 {len(proj_results)}개 + 요약"
    )

    # 전체 생성의 경우 207 Multi-Status 로 요약+프로젝트 목록을 반환
    return JSONResponse(
        status_code=207,
        content=AllReportsResponse(
            summary=summary_result,
            projects=proj_results,
        ).model_dump(),
    )


@app.get(
    "/api/reports",
    response_model=ReportResponse,
    responses={
        200: {"description": "다운로드 링크 재발급 완료"},
        404: {"description": "리포트가 스토리지에 존재하지 않음"},
        422: {"description": "쿼리 파라미터 형식 오류"},
        502: {"description": "스토리지 조회/링크 생성 실패"},
    },
    summary="기존 리포트 다운로드 링크 재발급",
    description=(
        "이미 생성된 리포트의 Presigned 다운로드 URL 을 재발급합니다.\n\n"
        "- `project_id` 미입력 시 전체 요약 리포트(`summary`)를 대상으로 합니다.\n"
        "- `month` 미입력 시 현재 달을 기준으로 합니다."
    ),
)
async def get_report(
    project_id: Optional[str] = Query(
        default=None,
        description="프로젝트 ID. 미입력 시 전체 요약 리포트",
        example="proj-001",
    ),
    month: Optional[str] = Query(
        default=None,
        description="대상 연월 (YYYY-MM). 미입력 시 이번 달",
        example="2026-05",
    ),
):
    # month 형식 검증
    if month is not None:
        try:
            y, m = map(int, month.split("-"))
            if not (1 <= m <= 12):
                raise ValueError
        except (ValueError, AttributeError):
            raise HTTPException(
                status_code=422,
                detail="month 는 YYYY-MM 형식이어야 합니다 (예: 2026-05)",
            )

    year, mon     = _parse_month(month)
    report_month  = f"{year:04d}-{mon:02d}"
    pid           = project_id if project_id is not None else "summary"

    logger.info(
        f"GET /api/reports  project_id={project_id!r}  month={report_month}"
    )

    s3_key = f"reports/{pid}/{report_month}/cost_report.md"

    if not _s3_object_exists(s3_key):
        raise HTTPException(
            status_code=404,
            detail=(
                f"리포트를 찾을 수 없습니다: "
                f"project_id={project_id!r}, month={report_month}\n"
                f"(S3 key: {s3_key})"
            ),
        )

    now = datetime.now(KST)
    url, expires_at = _presign(s3_key, now)

    return ReportResponse(
        report_url=url,
        file_path=s3_key,
        generated_at=now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        expires_at=expires_at,
    )


# ─────────────────────────────────────────────────────────────────
# 헬스체크
# ─────────────────────────────────────────────────────────────────
@app.get("/health", include_in_schema=False)
async def health():
    return {"status": "ok", "service": "cost-report-api"}


# ─────────────────────────────────────────────────────────────────
# 로컬 실행 진입점
# ─────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "api_server:app",
        host="0.0.0.0",
        port=int(os.environ.get("PORT", "8000")),
        reload=False,
    )
