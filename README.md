# scratch-report

> PostgreSQL `cost_events` 테이블을 조회하여 조직별·프로젝트별 이달 LLM 사용 비용을 집계하는 리포트 스크립트 모음입니다.

## 소개

이달 1일부터 현재까지의 `cost_events` 데이터를 집계하여 조직 단위 또는 프로젝트 단위의 LLM 비용 현황을 터미널 테이블로 출력합니다.
Google Chat 웹훅 연동을 통해 주기적인 알림 발송에도 활용할 수 있습니다.

### 주요 기능

- 조직별 이달 LLM 비용 집계 (`report_org_cost.sh`)
- 프로젝트별 이달 LLM 비용 집계 (`report_project_cost.sh`)
- **예산 한도 임박 알림** (`alert_budget.sh`) — 사용률이 임계값 이상인 조직 감지 후 웹훅 전송
- `--detail` 옵션으로 토큰 상세 내역(Input / Output / Cache Write / Cache Read) 추가 출력
- `--output` 옵션으로 리포트를 타임스탬프 파일로 저장
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
chmod +x report_org_cost.sh report_project_cost.sh alert_budget.sh
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
| `ALERT_THRESHOLD` | 선택 | 예산 알림 임계값(%) | `90` |

---

## 실행

### 조직별 비용 집계

```bash
# 기본 (조직명 / 이벤트 수 / 비용(USD) / 한도(USD) / 사용율(%))
DB_HOST=host DB_USER=user DB_PASSWORD=pass DB_NAME=dbname ./report_org_cost.sh

# 상세 (토큰 내역 + 비용(cents) + 날짜별 누적 포함)
DB_HOST=host DB_USER=user DB_PASSWORD=pass DB_NAME=dbname ./report_org_cost.sh --detail
DB_HOST=host DB_USER=user DB_PASSWORD=pass DB_NAME=dbname ./report_org_cost.sh -d

# 파일 저장 (cost-report/report_org_cost-YYYYMMDD_HHMM.txt 형식)
DB_HOST=host DB_USER=user DB_PASSWORD=pass DB_NAME=dbname \
  ./report_org_cost.sh --output cost-report
```

### 프로젝트별 비용 집계

```bash
# 기본 (조직명 / 프로젝트명 / 이벤트 수 / 비용(USD))
DB_HOST=host DB_USER=user DB_PASSWORD=pass DB_NAME=dbname ./report_project_cost.sh

# 상세
DB_HOST=host DB_USER=user DB_PASSWORD=pass DB_NAME=dbname ./report_project_cost.sh --detail
DB_HOST=host DB_USER=user DB_PASSWORD=pass DB_NAME=dbname ./report_project_cost.sh -d

# 파일 저장
DB_HOST=host DB_USER=user DB_PASSWORD=pass DB_NAME=dbname \
  ./report_project_cost.sh --output cost-report
```

### 예산 한도 임박 알림

임계값 이상 사용률을 기록한 조직을 감지하여 알림을 전송합니다.
임계값 미만이면 웹훅 전송을 생략합니다(stdout에만 출력).

```bash
# 기본 임계값 90% 사용 — stdout 출력만
DB_HOST=host DB_USER=user DB_PASSWORD=pass DB_NAME=dbname ./alert_budget.sh

# 임계값 80%로 조정
DB_HOST=host DB_USER=user DB_PASSWORD=pass DB_NAME=dbname \
  ./alert_budget.sh --threshold 80

# 웹훅 전송 + 파일 저장 (cron 권장 조합)
DB_HOST=host DB_USER=user DB_PASSWORD=pass DB_NAME=dbname \
  ./alert_budget.sh -t 90 \
    -w "https://chat.googleapis.com/v1/spaces/.../messages?key=..." \
    -o cost-report

# 환경변수로 전달 (cron 등)
DB_HOST=host DB_USER=user DB_PASSWORD=pass DB_NAME=dbname \
  ALERT_THRESHOLD=90 WEBHOOK_URL=https://... ./alert_budget.sh -o cost-report
```

#### 알림 수준 기준

| 사용률 | 아이콘 | 수준 |
|--------|--------|------|
| ≥ 100% | 🔴 | 한도 초과 |
| ≥ 95%  | 🔴 | 위험 |
| ≥ 90%  | 🟠 | 경고 |
| 임계값 이상 | 🟡 | 주의 |

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
| 기본 | 조직명, 이벤트 수, 비용(USD), 한도(USD), 사용율(%) |
| 기본 (프로젝트) | 조직명, 프로젝트명, 이벤트 수, 비용(USD), 조직 한도(USD), 사용율(%) |
| `--detail` 추가 | + Input 토큰, Output 토큰, Cache Write, Cache Read, 비용(cents) |
| `alert_budget` | 조직명, 비용(USD), 한도(USD), 사용율(%), 잔여(USD) |

---

## cron 설정 예시

```cron
# 매일 오전 9시 — 예산 알림 (90% 이상 시 Google Chat 전송)
0 9 * * * DB_HOST=host DB_USER=user DB_PASSWORD=pass DB_NAME=dbname \
  WEBHOOK_URL=https://... \
  /path/to/alert_budget.sh -t 90 -o /var/log/cost-report >> /var/log/alert_budget.log 2>&1

# 매일 오전 9시 — 조직별 리포트 파일 저장
0 9 * * * DB_HOST=host DB_USER=user DB_PASSWORD=pass DB_NAME=dbname \
  /path/to/report_org_cost.sh -o /var/log/cost-report >> /var/log/report_org.log 2>&1
```

---

## 대상 테이블

| 테이블 | 용도 |
|--------|------|
| `public.organizations` | 조직 정보 (이름 등) |
| `public.projects` | 프로젝트 정보 (조직 연결) |
| `public.cost_events` | LLM 호출당 토큰/비용 이벤트 |
| `public.budget_policies` | 조직별 월 예산 한도 |

집계 기간: `DATE_TRUNC('month', CURRENT_DATE)` ~ 현재 시각

---

## 프로젝트 구조

```
scratch-report/
├── report_org_cost.sh      # 조직별 비용 집계 스크립트
├── report_project_cost.sh  # 프로젝트별 비용 집계 스크립트
└── alert_budget.sh         # 예산 한도 임박 알림 스크립트
```
