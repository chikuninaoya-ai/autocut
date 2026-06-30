#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
サーバー内Whisper（faster-whisper）で音声→テロップ(SRT)を生成する。
APIキー不要。Cloud Runコンテナ内で完結。
- 動画から音声を 16kHz mono wav に変換
- faster-whisper で日本語文字起こし → SRT文字列
依存: ffmpeg / faster-whisper / 環境変数 WHISPER_MODEL（既定 small）
"""
import subprocess, os, tempfile

_MODEL = None

def get_model():
    global _MODEL
    if _MODEL is None:
        from faster_whisper import WhisperModel
        size = os.environ.get("WHISPER_MODEL", "small")
        _MODEL = WhisperModel(size, device="cpu", compute_type="int8")
    return _MODEL

def extract_audio(input_path):
    fd, wav = tempfile.mkstemp(suffix=".wav"); os.close(fd)
    r = subprocess.run(["ffmpeg","-y","-i",input_path,"-vn",
                        "-ac","1","-ar","16000","-f","wav", wav],
                       capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError("音声抽出に失敗:\n"+(r.stderr or "")[-800:])
    return wav

def _ts(t):
    if t < 0: t = 0
    h = int(t//3600); m = int((t%3600)//60); s = int(t%60); ms = int(round((t-int(t))*1000))
    if ms == 1000: s += 1; ms = 0
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"

def transcribe(input_path, language="ja", api_key=None):
    """SRT文字列を返す（api_keyは互換のため残すが未使用）"""
    wav = extract_audio(input_path)
    try:
        model = get_model()
        segments, info = model.transcribe(wav, language=language, vad_filter=True)
        out, i = [], 1
        for seg in segments:
            text = (seg.text or "").strip()
            if not text:
                continue
            out.append(str(i))
            out.append(f"{_ts(seg.start)} --> {_ts(seg.end)}")
            out.append(text)
            out.append("")
            i += 1
        return "\n".join(out)
    finally:
        try: os.remove(wav)
        except: pass

if __name__ == "__main__":
    import sys
    srt = transcribe(sys.argv[1])
    out = sys.argv[2] if len(sys.argv) > 2 else None
    if out:
        open(out, "w", encoding="utf-8").write(srt); print("wrote", out)
    else:
        print(srt)
