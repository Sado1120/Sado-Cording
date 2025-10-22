FROM python:3.11-slim

WORKDIR /app/frontend

# 정적 에셋을 컨테이너 루트에 직접 복사해 HTTP 서버가 바로 제공할 수 있도록 구성합니다.
COPY frontend/ ./

EXPOSE 8501

CMD ["python", "serve.py"]
