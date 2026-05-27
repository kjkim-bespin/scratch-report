# Agent Response
**Issue**: 비용 리포트 MD 문서 생성 및 다운로드 링크 제공
**Run**: 55373ecb-a455-46b9-95a0-f1ad5f62ac9a
**Date**: 2026-05-27T01:03:57.918Z
---커밋 완료. 전체 구현 내용을 정리합니다.

---

## 작업 결과 보고

### 구현 파일 목록

| 파일 | 역할 |
|---|---|
| `generate_cost_report_md.py` | MD 리포트 생성 + 스토리지 업로드 Python 구현체 |
| `generate_cost_report_md.sh` | Bash 래퍼 (의존성 확인 + 환경변수 설정) |
| `sql/cost_report_project.sql` | 프로젝트별 이달 집계 쿼리 |
| `sql/cost_report_prev_month.sql` | 전월 비교용 집계 쿼리 |
| `sql/cost_report_daily.sql` | 일별 비용 추이 쿼리 |
| `samples/cost_report_sample.md` | 실제 출력 형태의 샘플 리포트 |

---

### 핵심 설계 결정

#### 1. DB 조회 방식
기존 `psql` CLI 방식을 그대로 유지. `COPY (SELECT ...) TO STDOUT WITH (FORMAT CSV, HEADER)` 를 사용해 **추가 Python 패키지 없이 stdlib만으로** CSV 파싱을 처리합니다.

#### 2. 리포트 구성
각 프로젝트별로 독립 MD 파일 1개 + 전체 요약 파일 1개를 생성합니다:

```
reports/{project_id}/{YYYY-MM}/cost_report.md  ← 프로젝트 상세
reports/summary/{YYYY-MM}/cost_report.md       ← 전체 요약
```

각 프로젝트 리포트에는 **① 요약 (전월 대비 증감률 포함)**, **② 토큰 유형별 비용 항목 상세**, **③ 일별 추이** 3개 섹션이 포함됩니다.

#### 3. 스토리지 & 다운로드 링크
- AWS CLI(`aws s3 cp` + `aws s3 presign`) 단일 코드 경로로 **AWS S3 / MinIO 모두 지원**
- MinIO는 `STORAGE_ENDPOINT` 환경변수로 `--endpoint-url` 전달
- Presigned URL 유효기간 `REPORT_EXPIRE_DAYS`(기본 7일)
- 생성된 모든 링크를 `report_links_{YYYY-MM}.json`에도 저장 (파이프라인 연동용)

#### 4. `프로젝트 공통` 처리
`project_id IS NULL` 인 행은 `common_{org_id}` 로 합성된 ID를 부여해 경로 충돌 없이 처리합니다.

---

### 사용 예시

```bash
# 이달 리포트 생성 + S3 업로드
DB_HOST=host DB_USER=user DB_PASSWORD=pass DB_NAME=mydb \
STORAGE_BUCKET=my-reports-bucket \
./generate_cost_report_md.sh

# 특정 월 / 로컬만
./generate_cost_report_md.sh --month 2026-04 --no-upload --output-dir /tmp/reports

# MinIO
STORAGE_TYPE=minio STORAGE_ENDPOINT=http://minio:9000 \
STORAGE_BUCKET=cost-reports \
./generate_cost_report_md.sh
```