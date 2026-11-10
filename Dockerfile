# Runs the web app as one long-lived server, which processing needs (see README, "Deploy").
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    DJANGO_DEBUG=0 \
    DJANGO_ALLOWED_HOSTS=* \
    DJANGO_BEHIND_HTTPS_PROXY=1 \
    DJANGO_DB_PATH=/data/db.sqlite3 \
    TRAFFIC_MEDIA_ROOT=/data/media \
    TRAFFIC_WEIGHTS_DIR=/app/weights

# OpenCV needs these system libraries
RUN apt-get update \
    && apt-get install -y --no-install-recommends libgl1 libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# CPU-only PyTorch: the default build bundles about 4 GB of NVIDIA GPU libraries
COPY requirements.txt .
RUN pip install torch torchvision --index-url https://download.pytorch.org/whl/cpu \
    && pip install -r requirements.txt gunicorn

# Download the YOLO weights now, so the first analysis does not wait for them
RUN mkdir -p weights && cd weights \
    && python -c "from ultralytics import YOLO; [YOLO(m) for m in ('yolo26n.pt', 'yolo26s.pt', 'yolo26m.pt')]"

COPY . .
RUN python manage.py collectstatic --noinput && mkdir -p /data

EXPOSE 8000
# One worker process, because the processing lock and progress live in memory; threads serve requests.
CMD ["sh", "-c", "python manage.py migrate --noinput && exec gunicorn config.wsgi --bind 0.0.0.0:${PORT:-8000} --workers 1 --threads 8 --timeout 600"]
