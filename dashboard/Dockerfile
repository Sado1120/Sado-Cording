FROM python:3.11-slim
WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1
COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r requirements.txt
COPY src /app/src
COPY src/index.html /app/static/index.html
EXPOSE 8080
CMD ["uvicorn", "src.app:app", "--host", "0.0.0.0", "--port", "8080"]
