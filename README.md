# ILJIN Copilot

ILJIN Copilot은 업비트 코인 마켓과 미국 ETF 포트폴리오를 동시에 연구할 수 있는 자동매매 실험실입니다. 백엔드는 FastAPI 기반의 시뮬레이션 엔진을 제공하고, 프론트엔드는 감각적인 대시보드로 전략 지표와 리밸런싱 제안을 한눈에 보여줍니다.

## 구성 요소

- **backend/** – EMA 교차 전략, 포트폴리오 리밸런싱 로직, FastAPI 서비스
- **frontend/** – 최신 디자인의 싱글 페이지 대시보드 (Chart.js 기반 시각화 포함)
- **tests/** – 핵심 전략 함수 단위 테스트

## 빠른 시작

1. 의존성 설치
   ```bash
   python -m venv .venv
   source .venv/bin/activate
   pip install -r requirements.txt
   ```

2. 백엔드 API 실행
   ```bash
   uvicorn backend.app:app --reload
   ```

3. `frontend/index.html` 파일을 브라우저에서 열면 대시보드가 표시됩니다. (Live Server 또는 `python -m http.server` 사용 추천)

## 주요 기능

- **EMA 전략 시뮬레이션**: Fast/Slow EMA 조합, 수수료, 초기 자본을 조정하며 자동매매 전략 성능을 검증합니다.
- **시세 자동 생성**: 클릭 한 번으로 시드 기반 랜덤 시세를 생성하여 백테스트를 반복할 수 있습니다.
- **리밸런싱 추천**: 현재 보유 자산과 목표 비중을 입력하면 매수/매도 금액을 자동 계산합니다.
- **트레이드 로그**: 각 거래의 진입/청산, 수익률, PnL을 테이블로 제공합니다.
- **고급 UX**: Pretendard 폰트, 글래스모피즘 스타일, 반응형 레이아웃으로 고급스러운 컨트롤 센터 경험을 제공합니다.

## 테스트

단위 테스트는 `pytest`로 실행합니다.
```bash
pytest
```

## GitHub 업로드

모든 변경 사항을 커밋 후 GitHub 원격 저장소에 push 하면 자동으로 최신 코드가 공유됩니다.
```bash
git add .
git commit -m "feat: build iljin copilot dashboard"
git push origin <branch-name>
```
