FROM python:3.11-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    CLOUDMUSIC2KTV_HOST=0.0.0.0 \
    CLOUDMUSIC2KTV_PORT=7860 \
    CLOUDMUSIC2KTV_FONT_DIR=/usr/share/fonts/opentype/noto

RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg fonts-noto-cjk fonts-dejavu \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt ./requirements.txt
RUN python -m pip install -r requirements.txt

COPY app.py ./app.py
COPY cloudmusic2ktv ./cloudmusic2ktv
COPY static ./static
COPY templates ./templates
COPY README.md ./README.md
COPY ARCHITECTURE.md ./ARCHITECTURE.md

RUN useradd --create-home --uid 10001 --shell /usr/sbin/nologin app \
    && mkdir -p /app/instance /app/outputs \
    && chown -R app:app /app

USER app
EXPOSE 7860

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:7860/api/healthz', timeout=3)"

CMD ["gunicorn", "--bind", "0.0.0.0:7860", "--workers", "1", "--threads", "4", "--timeout", "0", "--access-logfile", "-", "--error-logfile", "-", "app:app"]
