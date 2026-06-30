#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
スマホ動画 自動カット（特典版）処理エンジン
- 無音検出（ffmpeg silencedetect）→ 残すセグメント算出
- （任意）SRTテロップを焼き込み
- 無音をカットして1本のMP4に書き出し

依存: ffmpeg / ffprobe（CLI）
テロップ自動生成（Whisper）は transcribe.py 側。本ファイルは「カット＋焼き込み」担当。
"""
import subprocess, re, json, os, argparse

def run(cmd, capture=True):
    return subprocess.run(cmd, capture_output=capture, text=True)

def ffprobe_duration(path):
    r = run(["ffprobe","-v","error","-show_entries","format=duration",
             "-of","default=nokey=1:noprint_wrappers=1", path])
    try: return float(r.stdout.strip())
    except: return 0.0

def detect_silence(path, noise_db=-40.0, min_sil=0.5):
    """ffmpeg silencedetect で無音区間 [(start,end),...] を取得"""
    r = run(["ffmpeg","-hide_banner","-i",path,
             "-af", f"silencedetect=noise={noise_db}dB:d={min_sil}",
             "-f","null","-"])
    log = r.stderr or ""
    starts = [float(m) for m in re.findall(r"silence_start:\s*([0-9.]+)", log)]
    ends   = [float(m) for m in re.findall(r"silence_end:\s*([0-9.]+)", log)]
    sil = []
    for i, s in enumerate(starts):
        e = ends[i] if i < len(ends) else None
        sil.append((s, e))
    return sil

def keep_segments(duration, silences, pad=0.08, pad_lead=None):
    if pad_lead is None: pad_lead = pad
    """無音区間の補集合＝残すセグメント。前後にパディングを付けてマージ。"""
    segs, cur = [], 0.0
    for (s, e) in silences:
        seg_end = s
        if seg_end > cur:
            segs.append([cur, seg_end])
        cur = e if e is not None else duration
    if cur < duration:
        segs.append([cur, duration])
    # パディング
    padded = []
    for a, b in segs:
        padded.append([max(0.0, a - pad_lead), min(duration, b + pad)])
    # マージ
    merged = []
    for a, b in padded:
        if merged and a <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], b)
        else:
            merged.append([a, b])
    # 極小セグメント除去
    merged = [s for s in merged if s[1]-s[0] > 0.05]
    return merged

def build_subtitles_filter(srt_path, font_name, fonts_dir, font_size):
    esc = srt_path.replace("\\","\\\\").replace(":","\\:").replace("'","\\'")
    style = []
    if font_name: style.append(f"FontName={font_name}")
    style.append(f"FontSize={font_size}")
    style += ["PrimaryColour=&H00FFFFFF","OutlineColour=&H00000000",
              "BorderStyle=1","Outline=2","Shadow=0","Alignment=2","MarginV=30"]
    sub = f"subtitles=filename='{esc}'"
    if fonts_dir: sub += f":fontsdir='{fonts_dir}'"
    sub += f":force_style='{','.join(style)}'"
    return sub

def process(input_path, output_path, noise_db=-48.0, min_sil=0.7, pad=0.12,
            srt_path=None, font_name=None, fonts_dir=None, font_size=24, cut=True):
    dur = ffprobe_duration(input_path)
    if dur <= 0:
        raise RuntimeError("動画の長さを取得できませんでした")
    if cut:
        sil = detect_silence(input_path, noise_db, min_sil)
        segs = keep_segments(dur, sil, pad, pad_lead=max(0.0, pad-0.0667))
        if not segs:
            segs = [[0.0, dur]]
    else:
        sil = []
        segs = [[0.0, dur]]   # 全体を保持（テロップのみ）
    kept = sum(b-a for a, b in segs)
    n = len(segs)

    # trim+concat方式（映像・音声とも正しくカット）。テロップは「焼き込み→trim」順で同期維持。
    fc = []
    use_sub = bool(srt_path and os.path.exists(srt_path))
    vlabels = "".join(f"[bv{i}]" for i in range(n))
    if use_sub:
        sub = build_subtitles_filter(srt_path, font_name, fonts_dir, font_size)
        fc.append(f"[0:v]{sub},split={n}{vlabels}")
    else:
        fc.append(f"[0:v]split={n}{vlabels}")
    fc.append("[0:a]asplit=%d%s" % (n, "".join(f"[ba{i}]" for i in range(n))))
    for i,(a,b) in enumerate(segs):
        fc.append(f"[bv{i}]trim={a:.3f}:{b:.3f},setpts=PTS-STARTPTS[v{i}]")
        fc.append(f"[ba{i}]atrim={a:.3f}:{b:.3f},asetpts=PTS-STARTPTS[a{i}]")
    concat_in = "".join(f"[v{i}][a{i}]" for i in range(n))
    fc.append(f"{concat_in}concat=n={n}:v=1:a=1[v][a]")
    filter_complex = ";".join(fc)

    cmd = ["ffmpeg","-y","-i",input_path,
           "-filter_complex", filter_complex,
           "-map","[v]","-map","[a]",
           "-c:v","libx264","-preset","veryfast","-pix_fmt","yuv420p",
           "-c:a","aac","-movflags","+faststart", output_path]
    r = run(cmd)
    if r.returncode != 0:
        raise RuntimeError("ffmpeg失敗:\n" + (r.stderr or "")[-1500:])
    out_dur = ffprobe_duration(output_path)
    return {
        "input_duration": round(dur,2),
        "output_duration": round(out_dur,2),
        "kept_estimate": round(kept,2),
        "reduction_pct": round((1-kept/dur)*100,1) if dur else 0,
        "segments": len(segs),
        "silences": len(sil),
    }

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("input")
    ap.add_argument("output")
    ap.add_argument("--noise", type=float, default=-40.0)
    ap.add_argument("--minsil", type=float, default=0.5)
    ap.add_argument("--pad", type=float, default=0.08)
    ap.add_argument("--srt", default=None)
    ap.add_argument("--font", default=None)
    ap.add_argument("--fontsdir", default=None)
    ap.add_argument("--fontsize", type=int, default=24)
    a = ap.parse_args()
    res = process(a.input, a.output, a.noise, a.minsil, a.pad,
                  a.srt, a.font, a.fontsdir, a.fontsize)
    print(json.dumps(res, ensure_ascii=False, indent=2))
