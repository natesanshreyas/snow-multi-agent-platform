FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

RUN useradd -m -u 1000 appuser && chown -R appuser:appuser /app
USER appuser

EXPOSE 8001

CMD ["python", "-m", "uvicorn", "demo_server:app", "--host", "0.0.0.0", "--port", "8001"]
