# scratch-report

> PostgreSQL `cost_events` 테이블을 조회하여 조직별·프로젝트별 이달 LLM 사용 비용을 집계하는 리포트 스크립트 모음입니다.

## 소개

이달 1일부터 현재까지의 `cost_events` 데이터를 집계하여 조직 단위 또는 프로젝트 단위의 LLM 비용 현황을 터미널 테이블로 출력합니다.
Google Chat 웹훅 연동을 통해 주기적인 알림 발송에도 활용할 수 있습니다.

### 주요 기능

- 조직별 이달 LLM 비용 집계 (`report_org_cost.sh`)
- 프로젝트별 이달 LLM 비용 집계 (`report_project_cost.sh`)
- `--detail` 옵션으로 토큰 상세 내역(Input / Output / Cache Write / Cache Read) + 일별 비용 추이 추가 출력
- `--webhook` 옵션 또는 `WEBHOOK_URL` 환경변수로 Google Chat 전송
- `budget_policies` 연동으로 조직 예산 한도 및 사용율 표시

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
# 기본 (조직명 / 이벤트 수 / 비용(USD) / 한도(USD) / 사용율(%))
DB_HOST=host DB_USER=user DB_PASSWORD=pass DB_NAME=dbname ./report_org_cost.sh

# 상세 (토큰 내역 + 비용(cents) + 일별 추이 포함)
DB_HOST=host DB_USER=user DB_PASSWORD=pass DB_NAME=dbname ./report_org_cost.sh --detail
DB_HOST=host DB_USER=user DB_PASSWORD=pass DB_NAME=dbname ./report_org_cost.sh -d
```

### 프로젝트별 비용 집계

```bash
# 기본 (조직명 / 프로젝트명 / 이벤트 수 / 비용(USD) / 조직 한도(USD) / 사용율(%))
DB_HOST=host DB_USER=user DB_PASSWORD=pass DB_NAME=dbname ./report_project_cost.sh

# 상세 (토큰 내역 + 비용(cents) + 일별 추이 포함)
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

| 스크립트 | 모드 | 컬럼 |
|----------|------|------|
| `report_org_cost.sh` | 기본 | 조직명, 이벤트 수, 비용(USD), 한도(USD), 사용율(%) |
| `report_org_cost.sh` | `--detail` | + Input 토큰, Output 토큰, Cache Write, Cache Read, 비용(cents) + 일별 추이 테이블 |
| `report_project_cost.sh` | 기본 | 조직명, 프로젝트명, 이벤트 수, 비용(USD), 조직 한도(USD), 사용율(%) |
| `report_project_cost.sh` | `--detail` | + Input 토큰, Output 토큰, Cache Write, Cache Read, 비용(cents) + 일별 추이 테이블 |

---

## 대상 테이블

| 테이블 | 용도 |
|--------|------|
| `public.organizations` | 조직 정보 (이름 등) |
| `public.projects` | 프로젝트 정보 (조직 연결) |
| `public.cost_events` | LLM 호출당 토큰/비용 이벤트 |
| `public.budget_policies` | 조직별 월간 예산 한도 (`monthly_limit_cents`) |

집계 기간: `DATE_TRUNC('month', CURRENT_DATE)` ~ 현재 시각

---

## 프로젝트 구조

```
scratch-report/
├── report_org_cost.sh        # 조직별 비용 집계 스크립트 (터미널 출력 / Webhook)
└── report_project_cost.sh    # 프로젝트별 비용 집계 스크립트 (터미널 출력 / Webhook)
```
