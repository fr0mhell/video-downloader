FROM python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    curl \
    ca-certificates \
    atomicparsley \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir \
    pycryptodomex \
    websockets \
    curl-cffi \
    mutagen \
    xattr

WORKDIR /app

COPY pyproject.toml .
COPY cli/ ./cli/
COPY src/ ./src/

RUN pip install --no-cache-dir -e .

RUN mkdir -p /downloads /cookies

WORKDIR /downloads

ENTRYPOINT ["video-loader"]
