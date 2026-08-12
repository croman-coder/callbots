# Base con CUDA + cuDNN: CTranslate2 (faster-whisper) necesita cuDNN 9 para GPU.
# La misma imagen funciona CPU-only con WHISPER_DEVICE=cpu; las libs quedan sin usar.
FROM nvidia/cuda:12.4.1-cudnn-runtime-ubuntu22.04

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    DEBIAN_FRONTEND=noninteractive \
    HF_HOME=/models/huggingface

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
        python3.10 \
        python3-pip \
        ca-certificates \
        curl \
    && ln -sf /usr/bin/python3.10 /usr/local/bin/python \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN python -m pip install --no-cache-dir --upgrade pip \
    && python -m pip install --no-cache-dir -r requirements.txt

COPY . .

RUN mkdir -p /models/piper /models/huggingface /recordings

EXPOSE 8090

CMD ["python", "-m", "app.main"]
