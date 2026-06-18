# Production image for the audio-repair ECS services.
# Same image runs both services; the ECS task `command` selects the mode:
#   ["serve","--mode","agent"]   or   ["serve","--mode","intake"]
FROM python:3.12-slim AS base

# ffmpeg/ffprobe are hard runtime deps (the repair tools shell out to them).
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg ca-certificates \
    && rm -rf /var/lib/apt/lists/*

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    WORK_DIR=/tmp/audio_repair

WORKDIR /app

# Install dependencies first (better layer caching), then the package.
COPY pyproject.toml ./
COPY src ./src
RUN pip install --no-cache-dir .

# Drop root.
RUN useradd --create-home --uid 10001 app \
    && mkdir -p /tmp/audio_repair \
    && chown -R app /app /tmp/audio_repair
USER app

# Fail fast if the binary or its ffmpeg dependency is missing.
HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
    CMD ffprobe -version >/dev/null 2>&1 && audio-repair --help >/dev/null 2>&1 || exit 1

ENTRYPOINT ["audio-repair"]
CMD ["serve", "--mode", "agent"]
