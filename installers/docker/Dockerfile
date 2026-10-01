# Ninaivu Lite in a container. Build context is the repository root:
#
#   docker compose -f installers/docker/docker-compose.yml up -d
#
# /photos is the library, mounted read-only (Ninaivu Lite only ever reads it);
# /data holds settings, people, the index and previews. There is no Control
# Panel here: `docker compose stop` and `start` do its job.
# The same Python every installer carries (.python-version; a test keeps them equal).
ARG PYTHON_VERSION=3.13.7
FROM python:${PYTHON_VERSION}-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# ffmpeg gives videos a preview picture; leave it out for a smaller image
# (docker build --build-arg WITH_FFMPEG=0) and videos get a plain tile.
ARG WITH_FFMPEG=1
RUN if [ "$WITH_FFMPEG" = "1" ]; then \
      apt-get update && apt-get install -y --no-install-recommends ffmpeg && \
      rm -rf /var/lib/apt/lists/* ; \
    fi

WORKDIR /app
COPY requirements.txt ./
RUN pip install -r requirements.txt
COPY ninaivu_lite ./ninaivu_lite
COPY README.md LICENSE ./

RUN groupadd -g 1000 ninaivu && useradd -u 1000 -g ninaivu -m ninaivu && \
    mkdir -p /photos /data && chown ninaivu:ninaivu /data
USER ninaivu:ninaivu

EXPOSE 8080
VOLUME ["/photos", "/data"]
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/healthz', timeout=3)" || exit 1
CMD ["python", "-m", "ninaivu_lite", "/photos", "--data", "/data", "--host", "0.0.0.0", "--port", "8080", "--no-browser"]
