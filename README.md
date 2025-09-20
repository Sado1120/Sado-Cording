# Sado Maintenance Copilot Stack

Synology Container Manager에서 24시간 운영 가능한 설비 보전/투자 코파일럿 참고 구성입니다.

## 구성 요소
- **API (FastAPI, :8000)**: 포트폴리오 제안, 설비 인사이트, 벡터스토어 연동.
- **Dashboard (FastAPI, :8080)**: 정적 대시보드 + API 프락시.
- **Scheduler (Python schedule)**: 주기적 데이터 인덱싱/캐시.
- **Vectorstore (선택, :9000)**: FAISS 기반 간단한 임베딩 검색. `docker compose --profile faiss` 로 활성화.

## 빠른 시작
```bash
# LF 유지, 실행권한 보존을 위해 Git 설정 권장
git config core.autocrlf false

# Synology Container Manager 또는 표준 Docker
docker compose up --build -d

# 벡터스토어까지 기동하려면
docker compose --profile faiss up --build -d
```

## 데이터 경로
- `./data` 디렉터리가 모든 컨테이너에 마운트됩니다.
- 권한 이슈 발생 시 Synology File Station에서 1000:1000 권한 부여 또는 `PUID/PGID` 환경변수를 추가하세요.
- 포트 충돌 시 `docker-compose.yml`의 `ports` 매핑을 수정해 호스트 포트만 바꿔주면 됩니다.

## 헬스체크
모든 서비스는 `python -c` 기반 헬스체크를 사용하므로 BusyBox 환경에서도 동작합니다.

## 개발 가이드
```bash
# API 로컬 실행
cd api && uvicorn src.main:app --reload --port 8000

# Scheduler 단발성 실행 (데이터 캐시 생성)
cd scheduler && python -m src.main --dry-run

# Dashboard 확인
cd dashboard && uvicorn src.app:app --reload --port 8080
```

## 테스트 데이터
`data/*.csv`에 샘플 ETF, 코인, 설비 로그가 포함되어 있으니 필요 시 교체하세요.
