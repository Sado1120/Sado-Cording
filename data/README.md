# Data Directory

- `raw/`: 원본 센서/시장 데이터 적재 위치
- `logs/`: 서비스 로그 및 스케줄러 상태 파일 저장
- `models/`: 학습된 모델 또는 벡터 인덱스 백업

스케줄러가 CSV 캐시를 생성하면 `etf_signals.csv`, `coin_signals.csv`, `maintenance_logs.csv` 등의 파일이 자동으로 채워집니다.
