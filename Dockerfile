# Образ сайта: Render (бесплатный Web Service) и, по желанию, Hugging Face Spaces (Docker SDK).
# LibreDWG по умолчанию НЕ компилируется — сборка из исходников долгая и съедает минуты пайплайна.
# Чтобы включить чтение DWG: docker build --build-arg BUILD_LIBREDWG=1
# На Render переменные окружения автоматически передаются как Docker build-arg,
# поэтому достаточно выставить BUILD_LIBREDWG=1 в Environment и пересобрать сервис.
# Слушает $PORT (Render задаёт сам) или 7860 (Hugging Face Spaces).

FROM python:3.12-slim-bookworm AS dwg-builder
ARG BUILD_LIBREDWG=0
ENV DEBIAN_FRONTEND=noninteractive
COPY scripts/install_dwg.sh /tmp/install_dwg.sh
RUN mkdir -p /opt/libredwg/bin \
    && if [ "$BUILD_LIBREDWG" = "1" ]; then \
         apt-get update \
         && apt-get install -y --no-install-recommends \
            build-essential curl ca-certificates tar gzip \
         && rm -rf /var/lib/apt/lists/* \
         && chmod +x /tmp/install_dwg.sh \
         && LIBREDWG_PREFIX=/opt/libredwg LIBREDWG_VERSION=0.13.3 bash /tmp/install_dwg.sh ; \
       else \
         echo "LibreDWG skipped (BUILD_LIBREDWG=${BUILD_LIBREDWG})" ; \
       fi

FROM python:3.12-slim-bookworm
ARG BUILD_LIBREDWG=0
ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    MPLBACKEND=Agg \
    HOST=0.0.0.0 \
    PORT=7860 \
    LIBREDWG_PREFIX=/opt/libredwg \
    TESSDATA_PREFIX=/usr/share/tesseract-ocr/5/tessdata \
    GOST_LIGHT_MEMORY=1 \
    GOST_MAX_IMAGE_SIDE=1600

RUN apt-get update && apt-get install -y --no-install-recommends \
        tesseract-ocr \
        tesseract-ocr-rus \
        libgl1 \
        libglib2.0-0 \
        libgomp1 \
        libxcb1 \
        fonts-dejavu-core \
        ca-certificates \
    && (apt-get install -y --no-install-recommends libredwg-tools || true) \
    && rm -rf /var/lib/apt/lists/* \
    && mkdir -p /usr/share/tesseract-ocr/5/tessdata \
    && if [ ! -f /usr/share/tesseract-ocr/5/tessdata/rus.traineddata ]; then \
         find /usr/share -name 'rus.traineddata' -exec cp {} /usr/share/tesseract-ocr/5/tessdata/ \; || true; \
       fi

COPY --from=dwg-builder /opt/libredwg /opt/libredwg
ENV PATH="/opt/libredwg/bin:${PATH}" \
    LD_LIBRARY_PATH="/opt/libredwg/lib"

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY gost_check.py .
COPY gost_checker ./gost_checker
COPY templates ./templates
COPY scripts ./scripts
COPY samples/titleblock_example.json ./samples/titleblock_example.json

EXPOSE 7860
CMD ["sh", "-c", "uvicorn gost_checker.web:app --host 0.0.0.0 --port ${PORT:-7860}"]
