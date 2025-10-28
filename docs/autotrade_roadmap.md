# Sado Trade Bot 자가 진단 & 프리미엄 자동매매 로드맵

본 문서는 자가 진단(AI Self-Check) 체계를 메이저급으로 확장하고, 실거래 수준의 프리미엄 자동매매 기능을 탑재하기 위한 단계별 계획을 제시합니다. 각 단계는 권장 우선순위, 핵심 작업, 예상 소요 시간(순수 개발 기준)을 포함합니다. 인력 1~2명이 전담한다고 가정한 보수적 추정입니다.

## 1. 진단 인프라 고도화 (2~3주)

| 단계 | 작업 | 설명 | 예상 소요 |
| --- | --- | --- | --- |
| 1.1 | **Self-Check 커버리지 확대** | `/diagnostics/ai/self-check`가 오토파일럿, 페이퍼 브로커, Upbit 네트워크, Synology Chat, AI 분석엔진 로그까지 스캔하도록 확장. 각 서브시스템 헬스 점수와 경고 ID 규격화. | 5일 |
| 1.2 | **스트리밍 진단 이벤트** | WebSocket 또는 Server-Sent Events로 상태 변화 실시간 스트리밍. 대시보드 진단 패널이 실시간 알림을 받아 반영하도록 구현. | 4일 |
| 1.3 | **자가 복구 플레이북 자동 실행** | 진단 결과에 따라 `market.refresh()`, `autopilot.reset()`, `chat.test()` 등 자동 복구 액션을 호출하는 룰 엔진 도입. 실패 시 롤백·알림. | 4일 |
| 1.4 | **이력 저장 및 리포트** | SQLite/InfluxDB 등에 진단 결과를 주기적으로 적재하고 일/주간 리포트를 생성. Synology Chat 다이제스트로 공유. | 3일 |

### 적용 방법
1. `backend/diagnostics.py` 모듈 신설 → 서브시스템 헬스체크 함수들을 `@register_check` 데코레이터로 등록.
2. `backend/app.py`에 WebSocket 엔드포인트 추가, FastAPI BackgroundTask로 주기 평가.
3. `frontend/app.js` 진단 패널에 이벤트 기반 상태 갱신 로직 도입.
4. `tests/test_diagnostics.py` 등 단위 테스트 추가, `/diagnostics/logs` API 응답 검증.

## 2. 프리미엄 자동매매 알고리즘 (3~4주)

| 단계 | 작업 | 설명 | 예상 소요 |
| --- | --- | --- | --- |
| 2.1 | **다중 전략 엔진** | EMA 외에 Breakout, Mean Reversion, Statistical Arbitrage 모듈 도입. 각 전략별 포지션 룰·위험 모델을 `StrategyProtocol`로 추상화. | 6일 |
| 2.2 | **AI 신호 통합기** | LSTM/Transformer 기반 가격 예측 또는 Gradient Boosted Trees를 이용해 전략 신뢰도를 보정. 온·오프라인 학습 파이프라인 구축. | 7일 |
| 2.3 | **급등·급락 사전 감지** | 변동성 클러스터링, 뉴스 센티먼트 이상치, 온체인 지표(가능 시) 등을 활용하여 조기 경고. 경고 발생 시 포지션 축소·헤지 룰 적용. | 5일 |
| 2.4 | **위험분산 포트폴리오** | 마켓 후보군을 섹터/상관관계 기준으로 클러스터링하고, Kelly/Black-Litterman 기반 배분 모델 구현. 오토파일럿이 자동 리밸런싱. | 5일 |

### 적용 방법
1. `backend/trading_strategies/` 디렉토리 생성, 전략별 클래스 구현.
2. `backend/autopilot.py`에서 전략 선택·믹싱 로직 추가 (`StrategyBlender`).
3. AI 신호 서버를 `backend/ai_models.py`로 분리, PyTorch/LightGBM 도입 시 `requirements.txt` 업데이트.
4. 백테스트·리스크 리포트 확장 (`tests/test_strategies.py`, `tests/test_risk_metrics.py`).

## 3. 운영 자동화 & 품질 확보 (2주)

| 단계 | 작업 | 설명 | 예상 소요 |
| --- | --- | --- | --- |
| 3.1 | **CI/CD 파이프라인** | GitHub Actions로 lint/pytest/도커 이미지 빌드/보안 스캔 자동화. Synology용 이미지 배포 스크립트 추가. | 4일 |
| 3.2 | **시뮬레이션 샌드박스** | 과거 데이터 리플레이, 시나리오 테스트(UI에서 날짜·조건 선택). 오토파일럿과 동일 로직으로 재현. | 5일 |
| 3.3 | **가드레일 & 경고** | API Rate Limit, 포지션 크기 제한, 최대 손실 알람 등을 정책화. Synology Chat 긴급 채널로 고우선 순위 메시지만 전송. | 3일 |

### 적용 방법
1. `.github/workflows/ci.yml` 구성, Docker Hub/NAS Registry로 푸시.
2. `backend/simulation.py` 모듈로 리플레이 엔진 제공, 프런트엔드에 샌드박스 탭 추가.
3. 알림 필터링 룰을 `backend/notifications.py`에서 중앙 관리, 테스트로 검증.

## 4. 확장 기능 로드맵 (장기, 4주+)

* **온보딩 위험 설문 & 전략 추천**: 신규 사용자가 위험 성향·목표를 입력하면 맞춤형 포트폴리오/전략을 자동 제안.
* **외부 시그널 통합**: 기관 리서치 API, 온체인 데이터, 파생상품 스프레드 등 고급 시그널 도입.
* **멀티 채널 알림**: Slack/Telegram/Email 등으로 확장, 메시지 우선순위 큐 도입.
* **시각화 업그레이드**: React/Next.js 기반 프런트엔드로 마이그레이션하여 컴포넌트 재사용 및 테마 지원 강화.

소요 기간은 기능 복잡도, 데이터 라이선스 획득 여부에 따라 1~2개월 이상을 예상합니다.

## 참고

* 각 단계 완료 후 반드시 `pytest`, 통합 테스트, Staging 환경 실거래 시뮬레이션을 수행하여 기능·안정성 검증을 반복해야 합니다.
* Synology 배포 환경에서는 Docker 이미지 재배포 및 `.env` 환경 변수 점검을 포함한 체크리스트를 운영하세요.

