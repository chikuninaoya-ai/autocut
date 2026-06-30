#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
スマホ動画 自動カット（特典版）APIサーバー
POST /process  : 動画アップロード → 無音カット(＋任意でテロップ) → MP4を返す
GET  /health   : 死活監視

環境変数:
  AUTOCUT_PASSWORD : 個別相談参加者に渡す解放パスワード（サーバー側で検証）
  OPENAI_API_KEY   : Whisper文字起こし用（テロップ有効時のみ必要）
  FONT_NAME        : 焼き込みフォント名（既定: Noto Sans JP）
  FONTS_DIR        : フォントディレクトリ（既定: ./fonts）
  ALLOW_ORIGIN     : CORS許可オリジン（既定: *）
"""
import os, tempfile, shutil, traceback
from fastapi import FastAPI, UploadFile, File, Form, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from starlette.background import BackgroundTask

import process_video, transcribe

HERE = os.path.dirname(os.path.abspath(__file__))
PASSWORD   = os.environ.get("AUTOCUT_PASSWORD", "")
FONT_NAME  = os.environ.get("FONT_NAME", "Noto Sans JP")
FONTS_DIR  = os.environ.get("FONTS_DIR", os.path.join(HERE, "fonts"))
ALLOW_ORIGIN = os.environ.get("ALLOW_ORIGIN", "*")
MAX_BYTES  = int(os.environ.get("MAX_BYTES", 300 * 1024 * 1024))  # 300MB

app = FastAPI(title="Sumaho AutoCut API")
app.add_middleware(CORSMiddleware, allow_origins=[ALLOW_ORIGIN] if ALLOW_ORIGIN!="*" else ["*"],
                   allow_methods=["*"], allow_headers=["*"])

@app.get("/health")
def health():
    return {"ok": True, "telop_ready": True}  # サーバー内Whisper（鍵不要）

@app.post("/process")
async def process(
    video: UploadFile = File(...),
    password: str = Form(...),
    telop: bool = Form(False),
    cut: bool = Form(True),
    noise_db: float = Form(-48.0),
    min_sil: float = Form(0.7),
):
    if not PASSWORD or password.strip().upper() != PASSWORD.strip().upper():
        raise HTTPException(status_code=401, detail="パスワードが違います")

    workdir = tempfile.mkdtemp(prefix="autocut_")
    in_path  = os.path.join(workdir, "input.mp4")
    out_path = os.path.join(workdir, "output.mp4")
    srt_path = None
    try:
        # 保存（サイズ上限）
        size = 0
        with open(in_path, "wb") as f:
            while True:
                chunk = await video.read(1024*1024)
                if not chunk: break
                size += len(chunk)
                if size > MAX_BYTES:
                    raise HTTPException(status_code=413, detail="動画が大きすぎます")
                f.write(chunk)

        # テロップ（任意・サーバー内Whisper）
        if telop:
            srt_text = transcribe.transcribe(in_path, language="ja")
            srt_path = os.path.join(workdir, "telop.srt")
            open(srt_path, "w", encoding="utf-8").write(srt_text)

        # カット（＋焼き込み）
        info = process_video.process(
            in_path, out_path,
            noise_db=noise_db, min_sil=min_sil, cut=cut,
            srt_path=srt_path,
            font_name=FONT_NAME, fonts_dir=FONTS_DIR, font_size=24,
        )
        if not os.path.exists(out_path):
            raise HTTPException(status_code=500, detail="書き出しに失敗しました")

        return FileResponse(
            out_path, media_type="video/mp4", filename="autocut.mp4",
            headers={"X-Autocut-Info": str(info)},
            background=BackgroundTask(shutil.rmtree, workdir, ignore_errors=True),
        )
    except HTTPException:
        shutil.rmtree(workdir, ignore_errors=True); raise
    except Exception as e:
        shutil.rmtree(workdir, ignore_errors=True)
        traceback.print_exc()
        return JSONResponse(status_code=500, content={"detail": str(e)})

# 静的フロント（LP・ツール）を同一サービスで配信（/ → index.html, /telop.html 等）
# ※ /health・/process は上で定義済みなので、ルートマウントはそれらを上書きしない
app.mount("/", StaticFiles(directory=os.path.join(HERE, "static"), html=True, check_dir=False), name="static")
