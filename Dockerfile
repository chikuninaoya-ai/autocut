FROM python:3.12-slim

# ffmpeg（カット/書き出し）＋ 日本語フォント（テロップ焼き込み）＋ libgomp1（ctranslate2）
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg fonts-noto-cjk libgomp1 \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY server/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Whisperモデルをイメージに焼き込み（起動時ダウンロードを回避）
ENV WHISPER_MODEL=small
RUN python -c "from faster_whisper import WhisperModel; WhisperModel('small', device='cpu', compute_type='int8')"

# サーバーコード + 静的フロント（LP・ツール・画像）を同梱
COPY server/ /app/
COPY index.html telop.html tool.html /app/static/
COPY assets/ /app/static/assets/

ENV PORT=8080
CMD ["sh","-c","uvicorn app:app --host 0.0.0.0 --port ${PORT}"]
