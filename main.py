import os
import json
import uuid
import asyncio
import subprocess
import tempfile
import shutil
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, UploadFile, File, Form, BackgroundTasks, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse, FileResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware

app = FastAPI(title="ClipForge")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

BASE_DIR = Path(__file__).parent
UPLOADS_DIR = BASE_DIR / "uploads"
OUTPUTS_DIR = BASE_DIR / "outputs"
STATIC_DIR  = BASE_DIR / "static"

for d in [UPLOADS_DIR, OUTPUTS_DIR, STATIC_DIR]:
    d.mkdir(exist_ok=True)

app.mount("/static",  StaticFiles(directory=str(STATIC_DIR)),  name="static")
app.mount("/outputs", StaticFiles(directory=str(OUTPUTS_DIR)), name="outputs")

jobs: dict = {}


# ── ROUTES ────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
async def index():
    return HTMLResponse(content=(STATIC_DIR / "index.html").read_text())


@app.post("/api/process")
async def process_video(
    background_tasks: BackgroundTasks,
    file: UploadFile           = File(...),
    clip_count: int            = Form(3),
    clip_duration: int         = Form(60),
    format: str                = Form("vertical"),
    clip_style: str            = Form("highlights"),
    subtitles: bool            = Form(True),
    subtitle_style: str        = Form("word_by_word"),
    subtitle_position: str     = Form("bottom"),
    subtitle_preset: str       = Form("white_bold"),
    remove_silence: bool       = Form(True),
    transition: str            = Form("hard_cut"),
    normalize_audio: bool      = Form(True),
    content_brief: str         = Form(""),
    anthropic_key: str         = Form(...),
    groq_key: str              = Form(""),
):
    job_id = str(uuid.uuid4())
    ext = Path(file.filename).suffix or ".mp4"
    input_path = UPLOADS_DIR / f"{job_id}{ext}"

    content = await file.read()
    input_path.write_bytes(content)

    jobs[job_id] = {"status": "queued", "progress": 0,
                    "message": "Video received.", "clips": [], "error": None}

    background_tasks.add_task(
        run_pipeline, job_id=job_id, input_path=str(input_path),
        clip_count=clip_count, clip_duration=clip_duration, format=format,
        clip_style=clip_style, subtitles=subtitles, subtitle_style=subtitle_style,
        subtitle_position=subtitle_position, subtitle_preset=subtitle_preset,
        remove_silence=remove_silence, transition=transition,
        normalize_audio=normalize_audio, content_brief=content_brief,
        anthropic_key=anthropic_key, groq_key=groq_key,
    )
    return JSONResponse({"job_id": job_id})


@app.get("/api/status/{job_id}")
async def get_status(job_id: str):
    if job_id not in jobs:
        raise HTTPException(status_code=404, detail="Job not found")
    return JSONResponse(jobs[job_id])


@app.get("/api/download/{filename}")
async def download_clip(filename: str):
    fp = OUTPUTS_DIR / filename
    if not fp.exists():
        raise HTTPException(status_code=404, detail="File not found")
    return FileResponse(str(fp), media_type="video/mp4", filename=filename)


# ── HELPERS ───────────────────────────────────

def upd(job_id, **kw):
    if job_id in jobs:
        jobs[job_id].update(kw)


def ffrun(cmd, timeout=300):
    """Run an ffmpeg command, return CompletedProcess."""
    return subprocess.run(cmd, capture_output=True, timeout=timeout)


def duration_of(path):
    try:
        r = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", path],
            capture_output=True, text=True, timeout=30)
        return float(r.stdout.strip())
    except:
        return None


# ── PIPELINE ──────────────────────────────────

async def run_pipeline(job_id, input_path, clip_count, clip_duration, format,
                       clip_style, subtitles, subtitle_style, subtitle_position,
                       subtitle_preset, remove_silence, transition,
                       normalize_audio, content_brief, anthropic_key, groq_key):
    try:
        upd(job_id, status="processing", progress=5, message="Reading video…")

        dur = duration_of(input_path)
        if not dur:
            raise Exception("Could not read video. Check FFmpeg is installed.")

        upd(job_id, progress=10, message=f"Video loaded ({int(dur)}s). Transcribing…")

        transcript = await transcribe(input_path, groq_key)

        upd(job_id, progress=42, message="Transcription done. Claude selecting clips…")

        segments = await select_clips(
            transcript=transcript, video_duration=dur,
            clip_count=clip_count, clip_duration=clip_duration,
            clip_style=clip_style, content_brief=content_brief,
            anthropic_key=anthropic_key,
        )

        upd(job_id, progress=62, message=f"Selected {len(segments)} clips. Rendering…")

        output_clips = []
        for i, seg in enumerate(segments):
            upd(job_id,
                progress=62 + int((i / len(segments)) * 34),
                message=f"Rendering clip {i+1}/{len(segments)}: \"{seg.get('title','Clip')}\"…")

            fname = f"{job_id}_clip{i+1}.mp4"
            out   = str(OUTPUTS_DIR / fname)

            await render_clip(
                input_path=input_path, output_path=out,
                start=seg["start"], end=seg["end"],
                format=format, subtitles=subtitles,
                subtitle_style=subtitle_style,
                subtitle_position=subtitle_position,
                subtitle_preset=subtitle_preset,
                remove_silence=remove_silence,
                transition=transition,
                normalize_audio=normalize_audio,
                subtitle_words=seg.get("words", []),
            )

            output_clips.append({
                "filename": fname,
                "title":    seg.get("title", f"Clip {i+1}"),
                "reason":   seg.get("reason", ""),
                "hook":     seg.get("hook", ""),
                "duration": round(seg["end"] - seg["start"], 1),
                "score":    seg.get("score", 0),
                "score_breakdown": seg.get("score_breakdown", {}),
                "start":    seg["start"],
                "end":      seg["end"],
            })

        upd(job_id, status="done", progress=100,
            message="All clips ready!", clips=output_clips)

    except Exception as e:
        upd(job_id, status="error", message=str(e), error=str(e))
    finally:
        try:
            os.remove(input_path)
        except:
            pass


# ── TRANSCRIPTION ─────────────────────────────

async def transcribe(input_path: str, groq_key: str) -> dict:
    if groq_key:
        try:
            return await transcribe_groq(input_path, groq_key)
        except:
            pass

    try:
        from faster_whisper import WhisperModel
        model = WhisperModel("base", device="cpu", compute_type="int8")
        segs, info = model.transcribe(input_path, word_timestamps=True)
        words, text = [], []
        for s in segs:
            text.append(s.text.strip())
            for w in (s.words or []):
                words.append({"word": w.word.strip(),
                               "start": round(w.start, 3),
                               "end":   round(w.end,   3)})
        return {"text": " ".join(text), "words": words, "language": info.language}
    except ImportError:
        raise Exception("faster-whisper not installed.")
    except Exception as e:
        raise Exception(f"Transcription failed: {e}")


async def transcribe_groq(input_path: str, groq_key: str) -> dict:
    import httpx
    with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as t:
        audio = t.name
    ffrun(["ffmpeg", "-y", "-i", input_path, "-ac", "1", "-ar", "16000", "-b:a", "64k", audio])
    try:
        data = open(audio, "rb").read()
        async with httpx.AsyncClient(timeout=120) as c:
            r = await c.post(
                "https://api.groq.com/openai/v1/audio/transcriptions",
                headers={"Authorization": f"Bearer {groq_key}"},
                data={"model": "whisper-large-v3", "response_format": "verbose_json",
                      "timestamp_granularities": "word"},
                files={"file": ("audio.mp3", data, "audio/mpeg")},
            )
        j = r.json()
        words = [{"word": w["word"], "start": w["start"], "end": w["end"]}
                 for w in j.get("words", [])]
        return {"text": j.get("text", ""), "words": words, "language": j.get("language", "en")}
    finally:
        try: os.remove(audio)
        except: pass


# ── AI CLIP SELECTION ─────────────────────────

STYLES = {
    "highlights":  "most engaging, viral-worthy highlights",
    "hook":        "moments with the strongest opening hook",
    "story":       "moments that form a complete mini story arc",
    "quote":       "powerful standalone quotes or soundbites",
    "educational": "moments that clearly teach something valuable",
    "emotional":   "moments with the most emotional authenticity",
}


async def select_clips(transcript, video_duration, clip_count, clip_duration,
                       clip_style, content_brief, anthropic_key) -> list:
    import httpx

    text  = transcript.get("text", "")
    words = transcript.get("words", [])

    if not text.strip():
        return split_evenly(video_duration, clip_count, clip_duration, words)

    wts   = " ".join(f"[{w['start']:.1f}]{w['word']}" for w in words[:600])
    style = STYLES.get(clip_style, STYLES["highlights"])
    brief = f"\nContent brief:\n{content_brief.strip()}" if content_brief.strip() else ""

    prompt = f"""You are a world-class short-form video editor.

Select {clip_count} clips (~{clip_duration}s each) from this transcript.
Video duration: {int(video_duration)}s
Style focus: {style}{brief}

Word-timestamped transcript:
{wts}

Full transcript:
{text[:4000]}

Return ONLY a JSON array with {clip_count} objects, each with:
- "start": float (seconds)
- "end": float (seconds, start + ~{clip_duration}s, max {video_duration})
- "title": string (4-6 word punchy title)
- "reason": string (one sentence why this works)
- "hook": string (exact opening phrase of the clip)
- "score": integer 1-10
- "score_breakdown": {{"hook": int, "retention": int, "shareability": int}}

Rules: no overlapping clips, start at sentence boundaries, never exceed {video_duration}s.
Return ONLY the JSON array."""

    async with httpx.AsyncClient(timeout=90) as c:
        r = await c.post(
            "https://api.anthropic.com/v1/messages",
            headers={"x-api-key": anthropic_key,
                     "anthropic-version": "2023-06-01",
                     "content-type": "application/json"},
            json={"model": "claude-sonnet-4-20250514", "max_tokens": 1500,
                  "messages": [{"role": "user", "content": prompt}]},
        )

    if r.status_code != 200:
        raise Exception(f"Claude API error {r.status_code}: {r.text[:200]}")

    raw = r.json()["content"][0]["text"].strip()
    if "```" in raw:
        raw = raw.split("```")[1]
        if raw.startswith("json"): raw = raw[4:]

    segs = json.loads(raw.strip())
    for s in segs:
        s["words"] = [w for w in words
                      if w["start"] >= s["start"] and w["end"] <= s["end"]]
    return segs


def split_evenly(duration, count, clip_len, words):
    step, segs = duration / count, []
    for i in range(count):
        st, en = i * step, min(i * step + clip_len, duration)
        segs.append({"start": round(st, 2), "end": round(en, 2),
                     "title": f"Clip {i+1}", "reason": "Auto-split",
                     "hook": "", "score": 5,
                     "score_breakdown": {"hook": 5, "retention": 5, "shareability": 5},
                     "words": [w for w in words if w["start"] >= st and w["end"] <= en]})
    return segs


# ── RENDERING ────────────────────────────────

# ASS colour format: &H00BBGGRR  (alpha=00)
PRESETS = {
    "white_bold": {"primary": "&H00FFFFFF", "outline": "&H00000000", "back": "&H80000000"},
    "yellow":     {"primary": "&H0000FFFF", "outline": "&H00000000", "back": "&H80000000"},
    "neon_green": {"primary": "&H0000FF7F", "outline": "&H00000000", "back": "&H80000000"},
}

# ASS alignment: 1=BL 2=BC 3=BR 4=ML 5=MC 6=MR 7=TL 8=TC 9=TR
POSITIONS = {
    "top":    {"alignment": 8, "marginv": 40},
    "middle": {"alignment": 5, "marginv": 0},
    "bottom": {"alignment": 2, "marginv": 60},
}


async def render_clip(input_path, output_path, start, end, format,
                      subtitles, subtitle_style, subtitle_position,
                      subtitle_preset, remove_silence, transition,
                      normalize_audio, subtitle_words):

    clip_dur = end - start

    with tempfile.TemporaryDirectory() as tmp:

        # 1. Cut
        raw = f"{tmp}/raw.mp4"
        ffrun(["ffmpeg", "-y",
               "-ss", str(start), "-i", input_path,
               "-t", str(clip_dur),
               "-c:v", "libx264", "-c:a", "aac", "-preset", "fast", raw])
        cur = raw

        # 2. Remove silence
        if remove_silence:
            out = f"{tmp}/nosilence.mp4"
            r = ffrun(["ffmpeg", "-y", "-i", cur,
                       "-af", "silenceremove=stop_periods=-1:stop_duration=0.4:stop_threshold=-42dB",
                       "-c:v", "copy", out])
            if r.returncode == 0 and os.path.exists(out) and os.path.getsize(out) > 1000:
                cur = out

        # 3. Normalize audio
        if normalize_audio:
            out = f"{tmp}/norm.mp4"
            r = ffrun(["ffmpeg", "-y", "-i", cur,
                       "-af", "loudnorm=I=-16:TP=-1.5:LRA=11",
                       "-c:v", "copy", out])
            if r.returncode == 0 and os.path.exists(out) and os.path.getsize(out) > 1000:
                cur = out

        # 4. Crop to format
        if format == "vertical":
            out = f"{tmp}/cropped.mp4"
            r = ffrun(["ffmpeg", "-y", "-i", cur,
                       "-vf", "crop=ih*9/16:ih,scale=1080:1920",
                       "-c:v", "libx264", "-c:a", "aac", "-preset", "fast", out])
            if r.returncode == 0 and os.path.exists(out) and os.path.getsize(out) > 1000:
                cur = out

        # 5. Transition
        if transition == "fade":
            actual = duration_of(cur) or clip_dur
            fade_out_st = max(0, actual - 0.4)
            out = f"{tmp}/faded.mp4"
            r = ffrun(["ffmpeg", "-y", "-i", cur,
                       "-vf",  f"fade=t=in:st=0:d=0.3,fade=t=out:st={fade_out_st:.2f}:d=0.3",
                       "-af",  f"afade=t=in:st=0:d=0.3,afade=t=out:st={fade_out_st:.2f}:d=0.3",
                       "-c:v", "libx264", "-preset", "fast", out])
            if r.returncode == 0 and os.path.exists(out) and os.path.getsize(out) > 1000:
                cur = out

        elif transition == "zoom_cut":
            out = f"{tmp}/zoom.mp4"
            # Detect resolution so zoompan works on any size
            probe = subprocess.run(
                ["ffprobe", "-v", "error", "-select_streams", "v:0",
                 "-show_entries", "stream=width,height",
                 "-of", "csv=p=0", cur],
                capture_output=True, text=True, timeout=15)
            try:
                w, h = map(int, probe.stdout.strip().split(","))
            except:
                w, h = 1080, 1920
            r = ffrun(["ffmpeg", "-y", "-i", cur,
                       "-vf", (f"zoompan=z='if(lte(on,20),zoom+0.002,1)':d=1"
                               f":x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)'"
                               f":s={w}x{h}:fps=30"),
                       "-c:v", "libx264", "-c:a", "copy", "-preset", "fast", out])
            if r.returncode == 0 and os.path.exists(out) and os.path.getsize(out) > 1000:
                cur = out

        # 6. Subtitles via ASS (no external font dependency)
        if subtitles and subtitle_words:
            wpl   = 1 if subtitle_style == "word_by_word" else 4
            srt   = f"{tmp}/subs.srt"
            ass   = f"{tmp}/subs.ass"
            out   = f"{tmp}/subbed.mp4"

            make_srt(subtitle_words, srt, start_offset=start, wpl=wpl)
            make_ass(srt, ass,
                     preset=PRESETS.get(subtitle_preset, PRESETS["white_bold"]),
                     pos=POSITIONS.get(subtitle_position, POSITIONS["bottom"]))

            r = ffrun(["ffmpeg", "-y", "-i", cur,
                       "-vf", f"ass={ass}",
                       "-c:v", "libx264", "-c:a", "copy", "-preset", "fast", out])
            if r.returncode == 0 and os.path.exists(out) and os.path.getsize(out) > 1000:
                cur = out

        shutil.copy2(cur, output_path)


# ── SUBTITLE HELPERS ──────────────────────────

def make_srt(words, path, start_offset=0, wpl=4):
    lines, i, idx = [], 0, 1
    while i < len(words):
        chunk = words[i:i + wpl]
        if not chunk: break
        st = max(0, chunk[0]["start"] - start_offset)
        en = max(st + 0.1, chunk[-1]["end"] - start_offset)
        txt = " ".join(w["word"] for w in chunk).strip()
        if txt:
            lines += [str(idx), f"{srt_ts(st)} --> {srt_ts(en)}", txt, ""]
            idx += 1
        i += wpl
    Path(path).write_text("\n".join(lines), encoding="utf-8")


def make_ass(srt_path, ass_path, preset, pos):
    """Convert SRT → ASS with embedded style. No external font needed."""
    primary = preset["primary"]
    outline = preset["outline"]
    back    = preset["back"]
    align   = pos["alignment"]
    marginv = pos["marginv"]

    header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: 1080
PlayResY: 1920

[V4+ Styles]
Format: Name,Fontname,Fontsize,PrimaryColour,SecondaryColour,OutlineColour,BackColour,Bold,Italic,Underline,StrikeOut,ScaleX,ScaleY,Spacing,Angle,BorderStyle,Outline,Shadow,Alignment,MarginL,MarginR,MarginV,Encoding
Style: Default,Arial,52,{primary},&H000000FF,{outline},{back},1,0,0,0,100,100,0,0,1,3,1,{align},40,40,{marginv},1

[Events]
Format: Layer,Start,End,Style,Name,MarginL,MarginR,MarginV,Effect,Text
"""
    events = []
    srt_text = Path(srt_path).read_text(encoding="utf-8")
    blocks = [b.strip() for b in srt_text.split("\n\n") if b.strip()]
    for block in blocks:
        ls = block.split("\n")
        if len(ls) < 3: continue
        times = ls[1]
        text  = " ".join(ls[2:])
        try:
            st_str, en_str = times.split(" --> ")
            st = srt_ts_to_ass(st_str.strip())
            en = srt_ts_to_ass(en_str.strip())
        except:
            continue
        # Escape ASS special chars
        text = text.replace("{", "").replace("}", "")
        events.append(f"Dialogue: 0,{st},{en},Default,,0,0,0,,{text}")

    Path(ass_path).write_text(header + "\n".join(events) + "\n", encoding="utf-8")


def srt_ts(s: float) -> str:
    h  = int(s // 3600)
    m  = int((s % 3600) // 60)
    sc = int(s % 60)
    ms = int((s % 1) * 1000)
    return f"{h:02d}:{m:02d}:{sc:02d},{ms:03d}"


def srt_ts_to_ass(ts: str) -> str:
    """00:00:01,234  →  0:00:01.23"""
    ts = ts.replace(",", ".")
    parts = ts.split(":")
    h, m, s = parts[0], parts[1], parts[2]
    s_val = float(s)
    return f"{int(h)}:{m}:{s_val:05.2f}"
