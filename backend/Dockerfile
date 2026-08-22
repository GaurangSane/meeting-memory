FROM python:3.11-slim

# Install ffmpeg
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .

RUN python -m pip install --no-cache-dir -r requirements.txt

# Make absolutely sure uvicorn is available
RUN python -m pip install --no-cache-dir "uvicorn[standard]==0.30.1"

COPY . .

CMD ["sh", "-c", "exec python -m uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000} --workers 4"]