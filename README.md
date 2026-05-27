# scratch-report

> PostgreSQL `cost_events` 테이블을 조회하여 조직별·프로젝트별 이달 LLM 사용 비용을 집계하는 리포트 스크립트 모음입니다.

## 소개

이달 1일부터 현재까지의 `cost_events` 데이터를 집계하여 조직 단위 또는 프로젝트 단위의 LLM 비용 현황을 터미널 테이블로 출력합니다.
Google Chat 웹훅 연동을 통해 주기적인 알림 발송에도 활용할 수 있습니다.

### 주요 기능

- 조직별 이달 LLM 비용 집계 (`report_org_cost.sh`)
- 프로젝트별 이달 LLM 비용 집계 (`report_project_cost.sh`)
- `--detail` 옵션으로 토큰 상세 내역(Input / Output / Cache Write / Cache Read) 추가 출력
- `--webhook` 옵션 또는 `WEBHOOK_URL` 환경변수로 Google Chat 전송

### 기술 스택

| 구분 | 기술 |
|------|------|
| 언어 | Bash (zsh 호환) |
| DB | PostgreSQL (`psql` CLI) |
| 알림 | Google Chat Incoming Webhook (`curl`) |
| JSON 직렬화 | Python 3 (`python3 -c`) |

---

## 시작하기

### 요구사항

- `psql` (PostgreSQL 클라이언트)
- `curl`
- `python3` (웹훅 사용 시)

### 설치

```bash
git clone https://github.com/kjkim-bespin/scratch-report.git
cd scratch-report
chmod +x report_org_cost.sh report_project_cost.sh
```

### 환경변수 설정

| 변수 | 필수 | 설명 | 기본값 |
|------|------|------|--------|
| `DB_HOST` | ✓ | PostgreSQL 호스트 | `localhost` |
| `DB_PORT` | 선택 | PostgreSQL 포트 | `5432` |
| `DB_USER` | ✓ | PostgreSQL 사용자 | `postgres` |
| `DB_PASSWORD` | ✓ | PostgreSQL 비밀번호 | - |
| `DB_NAME` | ✓ | 데이터베이스 이름 | `postgres` |
| `WEBHOOK_URL` | 선택 | Google Chat Incoming Webhook URL | - |

---

## 실행

### 조직별 비용 집계

```bash
# 기본 (조직명 / 이벤트 수 / 비용(USD))
DB_HOST=host DB_USER=user DB_PASSWORD=pass DB_NAME=dbname ./report_org_cost.sh

# 상세 (토큰 내역 + 비용(cents) 포함)
DB_HOST=host DB_USER=user DB_PASSWORD=pass DB_NAME=dbname ./report_org_cost.sh --detail
DB_HOST=host DB_USER=user DB_PASSWORD=pass DB_NAME=dbname ./report_org_cost.sh -d
```

### 프로젝트별 비용 집계

```bash
# 기본 (조직명 / 프로젝트명 / 이벤트 수 / 비용(USD))
DB_HOST=host DB_USER=user DB_PASSWORD=pass DB_NAME=dbname ./report_project_cost.sh

# 상세
DB_HOST=host DB_USER=user DB_PASSWORD=pass DB_NAME=dbname ./report_project_cost.sh --detail
DB_HOST=host DB_USER=user DB_PASSWORD=pass DB_NAME=dbname ./report_project_cost.sh -d
```

### Google Chat 웹훅 전송

```bash
# --webhook 플래그로 전달
DB_HOST=host DB_USER=user DB_PASSWORD=pass DB_NAME=dbname \
  ./report_org_cost.sh --webhook "https://chat.googleapis.com/v1/spaces/.../messages?key=..."

# 단축 플래그
DB_HOST=host DB_USER=user DB_PASSWORD=pass DB_NAME=dbname \
  ./report_project_cost.sh -d -w "https://chat.googleapis.com/..."

# 환경변수로 전달 (cron 등)
DB_HOST=host DB_USER=user DB_PASSWORD=pass DB_NAME=dbname WEBHOOK_URL=... ./report_org_cost.sh
```

### 출력 컬럼

| 모드 | 컬럼 |
|------|------|
| 기본 | 조직명, 이벤트 수, 비용(USD) |
| 기본 (프로젝트) | 조직명, 프로젝트명, 이벤트 수, 비용(USD) |
| `--detail` 추가 | + Input 토큰, Output 토큰, Cache Write, Cache Read, 비용(cents) |

---

## 대상 테이블

| 테이블 | 용도 |
|--------|------|
| `public.organizations` | 조직 정보 (이름 등) |
| `public.projects` | 프로젝트 정보 (조직 연결) |
| `public.cost_events` | LLM 호출당 토큰/비용 이벤트 |

집계 기간: `DATE_TRUNC('month', CURRENT_DATE)` ~ 현재 시각

---

---

## MD 리포트 생성 및 스토리지 업로드

### 개요

`generate_cost_report_md.sh` (Python 구현: `generate_cost_report_md.py`) 는 프로젝트별 월간 비용 리포트를 **Markdown 문서**로 생성하고, S3 / MinIO 스토리지에 업로드한 뒤 **다운로드 링크**를 반환합니다.

### 생성 파일 구조

| 파일 | 경로 | 설명 |
|------|------|------|
| 프로젝트 리포트 | `reports/{project_id}/{YYYY-MM}/cost_report.md` | 프로젝트별 상세 비용 내역 |
| 전체 요약 리포트 | `reports/summary/{YYYY-MM}/cost_report.md` | 모든 프로젝트 비용 합산 요약 |
| 링크 목록 | `report_links_{YYYY-MM}.json` | 생성된 모든 파일의 로컬 경로 및 다운로드 URL |

### 리포트 포함 내용

- 📊 **요약**: 이달/전월 비용, 전월 대비 증감률, 예산 사용율
- 🔢 **비용 항목별 상세**: Input / Output / Cache Write / Cache Read 토큰 유형별 사용량
- 📅 **일별 비용 추이**: 날짜별 이벤트 수, 토큰 수, 일별/누적 비용

### 추가 요구사항

| 도구 | 용도 |
|------|------|
| `python3` | 리포트 생성 (stdlib 전용, 추가 패키지 없음) |
| `aws` CLI | S3 업로드 및 Presigned URL 생성 |

### 추가 환경변수

| 변수 | 필수 | 설명 | 기본값 |
|------|------|------|--------|
| `STORAGE_TYPE` | 선택 | 스토리지 유형 (`s3` / `minio`) | `s3` |
| `STORAGE_BUCKET` | 업로드 시 필수 | 버킷 이름 | - |
| `STORAGE_ENDPOINT` | MinIO 사용 시 필수 | MinIO 엔드포인트 URL | - |
| `AWS_REGION` | 선택 | AWS 리전 | `ap-northeast-2` |
| `REPORT_EXPIRE_DAYS` | 선택 | 다운로드 링크 유효기간(일) | `7` |

### 실행 방법

```bash
# 이달 리포트 생성 + S3 업로드
DB_HOST=host DB_USER=user DB_PASSWORD=pass DB_NAME=dbname \
STORAGE_BUCKET=my-reports-bucket \
./generate_cost_report_md.sh

# 특정 연월 지정
DB_HOST=host DB_USER=user DB_PASSWORD=pass DB_NAME=dbname \
STORAGE_BUCKET=my-reports-bucket \
./generate_cost_report_md.sh --month 2026-04

# 로컬 파일만 생성 (업로드 없음)
DB_HOST=host DB_USER=user DB_PASSWORD=pass DB_NAME=dbname \
./generate_cost_report_md.sh --no-upload --output-dir /tmp/reports

# MinIO 사용
DB_HOST=host DB_USER=user DB_PASSWORD=pass DB_NAME=dbname \
STORAGE_TYPE=minio \
STORAGE_BUCKET=cost-reports \
STORAGE_ENDPOINT=http://minio.internal:9000 \
./generate_cost_report_md.sh
```

### 출력 예시

```
[INFO] 대상 기간    : 2026-05
[INFO] DB 연결      : localhost:5432/mydb
[INFO] 스토리지     : s3 / 버킷: my-reports-bucket
[INFO] 링크 유효기간: 7일

[INFO] 현재 달 데이터 조회 중 ...
[INFO] 전월 데이터 조회 중 ...
[INFO] 일별 데이터 조회 중 ...
[INFO] 로컬 저장: ./reports/proj-001/2026-05/cost_report.md
[OK] 업로드 완료: s3://my-reports-bucket/reports/proj-001/2026-05/cost_report.md
[OK] 다운로드 링크 (유효기간 7일): https://my-reports-bucket.s3.ap-northeast-2.amazonaws.com/...

================================================================
  리포트 생성 완료 (2026-05)
================================================================

  📄 Acme Corp / alpha-service
     로컬 파일  : ./reports/proj-001/2026-05/cost_report.md
     다운로드 URL (유효기간 7일):
     https://my-reports-bucket.s3.ap-northeast-2.amazonaws.com/reports/proj-001/2026-05/cost_report.md?...

  💾 링크 목록 JSON: ./report_links_2026-05.json
```

---

---

## REST API 서버

### 개요

`api_server.py` 는 `generate_cost_report_md.py` 의 리포트 생성 로직을 HTTP 엔드포인트로 노출하는 **FastAPI** 서버입니다.
사용자가 HTTP 요청 한 번으로 리포트를 생성하고 Presigned 다운로드 URL 을 받을 수 있습니다.

### 추가 의존성

```bash
pip install -r requirements.txt
# fastapi, uvicorn[standard], boto3
```

> **주의**: `generate_cost_report_md.sh` 가 `aws` CLI 를 사용하는 것과 달리,  
> API 서버는 **boto3** 를 통해 S3/MinIO 에 직접 접근합니다.  
> `aws` CLI 설치 없이도 동작합니다.

### 로컬 실행

```bash
# 1. 환경변수 설정
export DB_HOST=localhost
export DB_PORT=5432
export DB_USER=postgres
export DB_PASSWORD=secret
export DB_NAME=mydb

export STORAGE_BUCKET=my-reports-bucket
export AWS_REGION=ap-northeast-2
# MinIO 사용 시:
# export STORAGE_ENDPOINT=http://minio.internal:9000

# 2. 서버 기동
uvicorn api_server:app --host 0.0.0.0 --port 8000 --reload

# 또는 직접 실행
python3 api_server.py
```

서버 기동 후 `http://localhost:8000/docs` 에서 Swagger UI 를 확인할 수 있습니다.

---

### API 명세

#### `POST /api/reports/generate` — 리포트 생성

**요청**

```http
POST /api/reports/generate
Content-Type: application/json

{
  "project_id": "proj-001",  // 필수(nullable). null 이면 전체 프로젝트
  "month": "2026-05"         // 선택. 미입력 시 이번 달 (YYYY-MM)
}
```

**응답 — 특정 프로젝트 (`project_id` 지정 시, HTTP 200)**

```json
{
  "report_url":    "https://my-bucket.s3.ap-northeast-2.amazonaws.com/reports/proj-001/2026-05/cost_report.md?...",
  "file_path":     "reports/proj-001/2026-05/cost_report.md",
  "generated_at":  "2026-05-27T09:00:00Z",
  "expires_at":    "2026-06-03T09:00:00Z"
}
```

**응답 — 전체 프로젝트 (`project_id: null`, HTTP 207)**

```json
{
  "summary": {
    "report_url":   "https://...",
    "file_path":    "reports/summary/2026-05/cost_report.md",
    "generated_at": "2026-05-27T09:00:00Z",
    "expires_at":   "2026-06-03T09:00:00Z"
  },
  "projects": [
    {
      "report_url":   "https://...",
      "file_path":    "reports/proj-001/2026-05/cost_report.md",
      "generated_at": "2026-05-27T09:00:00Z",
      "expires_at":   "2026-06-03T09:00:00Z"
    }
  ]
}
```

**오류 코드**

| 코드 | 원인 |
|------|------|
| `404` | `project_id` 에 해당하는 비용 데이터 없음 |
| `422` | `month` 형식 오류 (YYYY-MM 이 아닌 경우) |
| `502` | S3/MinIO 업로드 또는 Presigned URL 생성 실패 |
| `503` | DB 연결 실패 또는 쿼리 타임아웃 (60초) |

---

#### `GET /api/reports` — 기존 리포트 다운로드 링크 재발급

이미 생성된 리포트가 스토리지에 존재하는 경우 Presigned URL 을 새로 발급합니다.

**요청**

```http
GET /api/reports?project_id=proj-001&month=2026-05
```

| 파라미터 | 필수 | 설명 |
|----------|------|------|
| `project_id` | 선택 | 프로젝트 ID. 미입력 시 전체 요약(`summary`) 대상 |
| `month` | 선택 | 대상 연월 (YYYY-MM). 미입력 시 이번 달 |

**응답 (HTTP 200)**

```json
{
  "report_url":   "https://...",
  "file_path":    "reports/proj-001/2026-05/cost_report.md",
  "generated_at": "2026-05-27T12:00:00Z",
  "expires_at":   "2026-06-03T12:00:00Z"
}
```

**오류 코드**

| 코드 | 원인 |
|------|------|
| `404` | 스토리지에 해당 리포트 파일이 없음 |
| `422` | `month` 형식 오류 |
| `502` | 스토리지 조회 또는 Presigned URL 생성 실패 |

---

#### `GET /health` — 헬스체크

```http
GET /health
→ {"status": "ok", "service": "cost-report-api"}
```

---

### S3 파일 경로 규칙

| 대상 | S3 Key |
|------|--------|
| 프로젝트별 리포트 | `reports/{project_id}/{YYYY-MM}/cost_report.md` |
| 전체 요약 리포트 | `reports/summary/{YYYY-MM}/cost_report.md` |

---

### cURL 예시

```bash
BASE=http://localhost:8000

# 1. 특정 프로젝트 리포트 생성
curl -s -X POST "$BASE/api/reports/generate" \
  -H "Content-Type: application/json" \
  -d '{"project_id":"proj-001","month":"2026-05"}' | jq .

# 2. 전체 프로젝트 리포트 생성 (project_id=null)
curl -s -X POST "$BASE/api/reports/generate" \
  -H "Content-Type: application/json" \
  -d '{"project_id":null,"month":"2026-05"}' | jq .

# 3. 이미 생성된 리포트 링크 재발급
curl -s "$BASE/api/reports?project_id=proj-001&month=2026-05" | jq .

# 4. 전체 요약 리포트 링크 재발급
curl -s "$BASE/api/reports?month=2026-05" | jq .
```

---

## 프로젝트 구조

```
scratch-report/
├── api_server.py                  # ★ REST API 서버 (FastAPI)
├── requirements.txt               # API 서버 Python 의존성
├── report_org_cost.sh             # 조직별 비용 집계 스크립트 (터미널 출력 / Webhook)
├── report_project_cost.sh         # 프로젝트별 비용 집계 스크립트 (터미널 출력 / Webhook)
├── generate_cost_report_md.sh     # MD 리포트 생성 + 스토리지 업로드 (Bash 래퍼)
├── generate_cost_report_md.py     # MD 리포트 생성 구현체 (Python, API 서버가 내부 모듈로 재사용)
├── sql/
│   ├── cost_report_project.sql    # 프로젝트별 이달 집계 쿼리 (참조용)
│   ├── cost_report_prev_month.sql # 전월 집계 쿼리 (참조용)
│   └── cost_report_daily.sql      # 일별 집계 쿼리 (참조용)
└── samples/
    └── cost_report_sample.md      # 샘플 리포트 출력 예시
```
