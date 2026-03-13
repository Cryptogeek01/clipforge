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
STATIC_DIR = BASE_DIR / "static"

for d in [UPLOADS_DIR, OUTPUTS_DIR, STATIC_DIR]:
    d.mkdir(exist_ok=True)

app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
app.mount("/outputs", StaticFiles(directory=str(OUTPUTS_DIR)), name="outputs")

jobs = {}


@app.get("/", response_class=HTMLResponse)
async def index():
    return HTMLResponse(content=(BASE_DIR / "static" / "index.html").read_text())


@app.post("/api/process")
async def process_video(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    # Clip settings
    clip_count: int = Form(3),
    clip_duration: int = Form(60),
    format: str = Form("vertical"),
    clip_style: str = Form("highlights"),        # highlights | hook | story | quote | educational | emotional
    # Subtitle settings
    subtitles: bool = Form(True),
    subtitle_style: str = Form("word_by_word"),  # word_by_word | static
    subtitle_position: str = Form("bottom"),     # top | middle | bottom
    subtitle_preset: str = Form("white_bold"),   # white_bold | yellow | neon_green
    # Enhancements
    remove_silence: bool = Form(True),
    transition: str = Form("hard_cut"),          # hard_cut | zoom_cut | fade
    music_vibe: str = Form("none"),              # none | energetic | chill | dramatic
    normalize_audio: bool = Form(True),
    # Intelligence
    content_brief: str = Form(""),               # open-ended brief about audience/platform/style
    # Keys
    anthropic_key: str = Form(...),
    groq_key: str = Form(""),                    # optional, for faster transcription
):
    job_id = str(uuid.uuid4())
    ext = Path(file.filename).suffix or ".mp4"
    input_path = UPLOADS_DIR / f"{job_id}{ext}"

    content = await file.read()
    with open(input_path, "wb") as f:
        f.write(content)

    jobs[job_id] = {
        "status": "queued",
        "progress": 0,
        "message": "Video received. Starting pipeline...",
        "clips": [],
        "error": None,
    }

    background_tasks.add_task(
        run_pipeline,
        job_id=job_id,
        input_path=str(input_path),
        clip_count=clip_count,
        clip_duration=clip_duration,
        format=format,
        clip_style=clip_style,
        subtitles=subtitles,
        subtitle_style=subtitle_style,
        subtitle_position=subtitle_position,
        subtitle_preset=subtitle_preset,
        remove_silence=remove_silence,
        transition=transition,
        music_vibe=music_vibe,
        normalize_audio=normalize_audio,
        content_brief=content_brief,
        anthropic_key=anthropic_key,
        groq_key=groq_key,
    )

    return JSONResponse({"job_id": job_id})


@app.get("/api/status/{job_id}")
async def get_status(job_id: str):
    if job_id not in jobs:
        raise HTTPException(status_code=404, detail="Job not found")
    return JSONResponse(jobs[job_id])


@app.get("/api/download/{filename}")
async def download_clip(filename: str):
    file_path = OUTPUTS_DIR / filename
    if not file_path.exists():
        raise HTTPException(status_code=404, detail="File not found")
    return FileResponse(str(file_path), media_type="video/mp4", filename=filename)


def update_job(job_id: str, **kwargs):
    if job_id in jobs:
        jobs[job_id].update(kwargs)


# ─────────────────────────────────────────────
# PIPELINE
# ─────────────────────────────────────────────

async def run_pipeline(job_id, input_path, clip_count, clip_duration, format,
                       clip_style, subtitles, subtitle_style, subtitle_position,
                       subtitle_preset, remove_silence, transition, music_vibe,
                       normalize_audio, content_brief, anthropic_key, groq_key):
    try:
        update_job(job_id, status="processing", progress=5, message="Reading video file...")

        duration = get_video_duration(input_path)
        if not duration:
            raise Exception("Could not read video. Ensure FFmpeg is installed correctly.")

        update_job(job_id, progress=10, message=f"Video loaded ({int(duration)}s). Transcribing audio — this takes a moment...")

        transcript_data = await transcribe_video(input_path, groq_key)

        update_job(job_id, progress=42, message="Transcription complete. Claude is analysing your video...")

        segments = await select_clips_with_ai(
            transcript=transcript_data,
            video_duration=duration,
            clip_count=clip_count,
            clip_duration=clip_duration,
            clip_style=clip_style,
            content_brief=content_brief,
            anthropic_key=anthropic_key,
        )

        update_job(job_id, progress=62, message=f"AI selected {len(segments)} clips. Rendering...")

        output_clips = []
        for i, seg in enumerate(segments):
            update_job(
                job_id,
                progress=62 + int((i / len(segments)) * 34),
                message=f"Rendering clip {i+1}/{len(segments)}: \"{seg.get('title', 'Clip')}\"..."
            )

            clip_filename = f"{job_id}_clip{i+1}.mp4"
            clip_output = str(OUTPUTS_DIR / clip_filename)

            await render_clip(
                input_path=input_path,
                output_path=clip_output,
                start=seg["start"],
                end=seg["end"],
                format=format,
                subtitles=subtitles,
                subtitle_style=subtitle_style,
                subtitle_position=subtitle_position,
                subtitle_preset=subtitle_preset,
                remove_silence=remove_silence,
                transition=transition,
                music_vibe=music_vibe,
                normalize_audio=normalize_audio,
                subtitle_words=seg.get("words", []),
            )

            output_clips.append({
                "filename": clip_filename,
                "title": seg.get("title", f"Clip {i+1}"),
                "reason": seg.get("reason", ""),
                "hook": seg.get("hook", ""),
                "duration": round(seg["end"] - seg["start"], 1),
                "score": seg.get("score", 0),
                "score_breakdown": seg.get("score_breakdown", {}),
                "start": seg["start"],
                "end": seg["end"],
            })

        update_job(job_id, status="done", progress=100, message="All clips ready!", clips=output_clips)

    except Exception as e:
        update_job(job_id, status="error", message=str(e), error=str(e))
    finally:
        try:
            os.remove(input_path)
        except:
            pass


# ─────────────────────────────────────────────
# VIDEO UTILS
# ─────────────────────────────────────────────

def get_video_duration(input_path: str) -> Optional[float]:
    try:
        result = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", input_path],
            capture_output=True, text=True, timeout=30
        )
        return float(result.stdout.strip())
    except:
        return None


# ─────────────────────────────────────────────
# TRANSCRIPTION
# ─────────────────────────────────────────────

async def transcribe_video(input_path: str, groq_key: str = "") -> dict:
    # Try Groq first (faster, free tier)
    if groq_key:
        try:
            return await transcribe_with_groq(input_path, groq_key)
        except Exception as e:
            pass  # Fall through to local Whisper

    # Local faster-whisper
    try:
        from faster_whisper import WhisperModel
        model = WhisperModel("base", device="cpu", compute_type="int8")
        segments, info = model.transcribe(input_path, word_timestamps=True)

        all_words, full_text = [], []
        for segment in segments:
            full_text.append(segment.text.strip())
            if hasattr(segment, "words") and segment.words:
                for word in segment.words:
                    all_words.append({
                        "word": word.word.strip(),
                        "start": round(word.start, 3),
                        "end": round(word.end, 3),
                    })

        return {"text": " ".join(full_text), "words": all_words, "language": info.language}

    except ImportError:
        raise Exception("faster-whisper not installed. Run: pip install faster-whisper")
    except Exception as e:
        raise Exception(f"Transcription failed: {str(e)}")


async def transcribe_with_groq(input_path: str, groq_key: str) -> dict:
    import httpx

    # Extract audio first
    with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as tmp:
        audio_path = tmp.name

    subprocess.run(
        ["ffmpeg", "-y", "-i", input_path, "-ac", "1", "-ar", "16000",
         "-b:a", "64k", audio_path],
        capture_output=True, timeout=120
    )

    try:
        with open(audio_path, "rb") as f:
            audio_data = f.read()

        async with httpx.AsyncClient(timeout=120) as client:
            response = await client.post(
                "https://api.groq.com/openai/v1/audio/transcriptions",
                headers={"Authorization": f"Bearer {groq_key}"},
                data={"model": "whisper-large-v3", "response_format": "verbose_json", "timestamp_granularities": "word"},
                files={"file": ("audio.mp3", audio_data, "audio/mpeg")},
            )
            data = response.json()

        words = []
        for w in data.get("words", []):
            words.append({"word": w["word"], "start": w["start"], "end": w["end"]})

        return {"text": data.get("text", ""), "words": words, "language": data.get("language", "en")}
    finally:
        try:
            os.remove(audio_path)
        except:
            pass


# ─────────────────────────────────────────────
# AI CLIP SELECTION
# ─────────────────────────────────────────────

STYLE_DESCRIPTIONS = {
    "highlights":   "most engaging, high-retention, viral-worthy moments",
    "hook":         "moments with the strongest opening hook — a statement that instantly grabs attention",
    "story":        "moments that tell a complete mini-story with a beginning, middle, and end",
    "quote":        "powerful standalone quotes or soundbites that work without context",
    "educational":  "moments that teach something clear and valuable",
    "emotional":    "moments with the most emotional weight, authenticity, or vulnerability",
}

async def select_clips_with_ai(transcript, video_duration, clip_count, clip_duration,
                                clip_style, content_brief, anthropic_key) -> list:
    import httpx

    text = transcript.get("text", "")
    words = transcript.get("words", [])

    if not text.strip():
        return split_evenly(video_duration, clip_count, clip_duration, words)

    word_ts = " ".join([f"[{w['start']:.1f}]{w['word']}" for w in words[:600]])
    style_desc = STYLE_DESCRIPTIONS.get(clip_style, STYLE_DESCRIPTIONS["highlights"])

    brief_section = ""
    if content_brief.strip():
        brief_section = f"""
Content Brief (use this to personalise clip selection):
{content_brief.strip()}
"""

    prompt = f"""You are a world-class video editor who creates viral short-form content.

Your task: Analyse this video transcript and select the {clip_count} best clips.
Each clip should be approximately {clip_duration} seconds long.
Video duration: {int(video_duration)} seconds.

Clip style focus: {style_desc}
{brief_section}

Word-timestamped transcript (format: [seconds]word):
{word_ts}

Full transcript:
{text[:4000]}

Return ONLY a valid JSON array with exactly {clip_count} objects. Each object must have:
- "start": float — start time in seconds
- "end": float — end time in seconds (start + ~{clip_duration}s, never exceed {video_duration})
- "title": string — punchy 4-6 word title for this clip
- "reason": string — one sentence: why this moment works for the stated style/brief
- "hook": string — the exact first sentence or phrase that opens this clip (the hook)
- "score": integer 1-10 — overall virality/quality score
- "score_breakdown": object with keys "hook" (1-10), "retention" (1-10), "shareability" (1-10)

Rules:
- Clips must NOT overlap
- Start each clip at a natural sentence boundary
- Prioritise clips that start with a strong statement, not mid-thought
- If a content brief is provided, weight selections toward that audience and platform

Return ONLY the JSON array. No markdown, no explanation, no code fences."""

    async with httpx.AsyncClient(timeout=90) as client:
        response = await client.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": anthropic_key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json={
                "model": "claude-sonnet-4-20250514",
                "max_tokens": 1500,
                "messages": [{"role": "user", "content": prompt}],
            }
        )

    if response.status_code != 200:
        raise Exception(f"Claude API error {response.status_code}: {response.text[:300]}")

    data = response.json()
    raw = data["content"][0]["text"].strip()

    if "```" in raw:
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]

    segments = json.loads(raw.strip())

    for seg in segments:
        seg["words"] = [w for w in words if w["start"] >= seg["start"] and w["end"] <= seg["end"]]

    return segments


def split_evenly(duration, count, clip_len, words):
    step = duration / count
    segments = []
    for i in range(count):
        start = i * step
        end = min(start + clip_len, duration)
        segments.append({
            "start": round(start, 2), "end": round(end, 2),
            "title": f"Clip {i+1}", "reason": "Auto-split (no speech detected)",
            "hook": "", "score": 5,
            "score_breakdown": {"hook": 5, "retention": 5, "shareability": 5},
            "words": [w for w in words if w["start"] >= start and w["end"] <= end],
        })
    return segments


# ─────────────────────────────────────────────
# RENDERING
# ─────────────────────────────────────────────

SUBTITLE_PRESETS = {
    "white_bold":  {"color": "&H00FFFFFF", "outline": "&H00000000", "outline_w": 2},
    "yellow":      {"color": "&H0000FFFF", "outline": "&H00000000", "outline_w": 2},
    "neon_green":  {"color": "&H0000FF7F", "outline": "&H00000000", "outline_w": 2},
}

POSITION_ALIGN = {
    "top":    ("8", "40"),
    "middle": ("5", "0"),
    "bottom": ("2", "60"),
}


async def render_clip(input_path, output_path, start, end, format,
                      subtitles, subtitle_style, subtitle_position,
                      subtitle_preset, remove_silence, transition,
                      music_vibe, normalize_audio, subtitle_words):

    duration = end - start

    with tempfile.TemporaryDirectory() as tmpdir:

        # 1. Cut raw clip
        raw = os.path.join(tmpdir, "raw.mp4")
        subprocess.run([
            "ffmpeg", "-y", "-ss", str(start), "-i", input_path,
            "-t", str(duration), "-c:v", "libx264", "-c:a", "aac",
            "-preset", "fast", raw
        ], capture_output=True, timeout=180)

        current = raw

        # 2. Silence removal
        if remove_silence:
            silenced = os.path.join(tmpdir, "silenced.mp4")
            r = subprocess.run([
                "ffmpeg", "-y", "-i", current,
                "-af", "silenceremove=stop_periods=-1:stop_duration=0.4:stop_threshold=-42dB",
                "-c:v", "copy", silenced
            ], capture_output=True, timeout=180)
            if r.returncode == 0 and os.path.exists(silenced):
                current = silenced

        # 3. Audio normalization
        if normalize_audio:
            normalized = os.path.join(tmpdir, "normalized.mp4")
            r = subprocess.run([
                "ffmpeg", "-y", "-i", current,
                "-af", "loudnorm=I=-16:TP=-1.5:LRA=11",
                "-c:v", "copy", normalized
            ], capture_output=True, timeout=180)
            if r.returncode == 0 and os.path.exists(normalized):
                current = normalized

        # 4. Format / crop
        if format == "vertical":
            cropped = os.path.join(tmpdir, "cropped.mp4")
            r = subprocess.run([
                "ffmpeg", "-y", "-i", current,
                "-vf", "crop=ih*9/16:ih,scale=1080:1920",
                "-c:v", "libx264", "-c:a", "aac", "-preset", "fast", cropped
            ], capture_output=True, timeout=180)
            if r.returncode == 0 and os.path.exists(cropped):
                current = cropped

        # 5. Transition effects (zoom cut on start)
        if transition == "zoom_cut":
            zoomed = os.path.join(tmpdir, "zoomed.mp4")
            r = subprocess.run([
                "ffmpeg", "-y", "-i", current,
                "-vf", "zoompan=z='if(lte(on,15),zoom+0.003,zoom)':d=1:x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':s=1080x1920",
                "-c:v", "libx264", "-c:a", "copy", "-preset", "fast", zoomed
            ], capture_output=True, timeout=180)
            if r.returncode == 0 and os.path.exists(zoomed):
                current = zoomed
        elif transition == "fade":
            faded = os.path.join(tmpdir, "faded.mp4")
            actual_dur = get_video_duration(current) or duration
            r = subprocess.run([
                "ffmpeg", "-y", "-i", current,
                "-vf", f"fade=t=in:st=0:d=0.3,fade=t=out:st={max(0, actual_dur-0.3):.2f}:d=0.3",
                "-af", f"afade=t=in:st=0:d=0.3,afade=t=out:st={max(0, actual_dur-0.3):.2f}:d=0.3",
                "-c:v", "libx264", "-preset", "fast", faded
            ], capture_output=True, timeout=180)
            if r.returncode == 0 and os.path.exists(faded):
                current = faded

        # 6. Subtitles
        if subtitles and subtitle_words:
            srt_path = os.path.join(tmpdir, "subs.srt")
            subtitled = os.path.join(tmpdir, "subtitled.mp4")

            words_per_line = 1 if subtitle_style == "word_by_word" else 4
            generate_srt(subtitle_words, srt_path, start_offset=start, words_per_line=words_per_line)

            preset = SUBTITLE_PRESETS.get(subtitle_preset, SUBTITLE_PRESETS["white_bold"])
            align, margin_v = POSITION_ALIGN.get(subtitle_position, POSITION_ALIGN["bottom"])

            style = (
                f"FontName=Montserrat,FontSize=26,Bold=1,"
                f"PrimaryColour={preset['color']},"
                f"OutlineColour={preset['outline']},"
                f"Outline={preset['outline_w']},Shadow=1,"
                f"Alignment={align},MarginV={margin_v}"
            )

            srt_escaped = srt_path.replace("\\", "/").replace(":", "\\:")
            r = subprocess.run([
                "ffmpeg", "-y", "-i", current,
                "-vf", f"subtitles={srt_escaped}:force_style='{style}'",
                "-c:v", "libx264", "-c:a", "copy", "-preset", "fast", subtitled
            ], capture_output=True, timeout=180)
            if r.returncode == 0 and os.path.exists(subtitled):
                current = subtitled

        # 7. Copy final output
        shutil.copy2(current, output_path)


def generate_srt(words, output_path, start_offset=0, words_per_line=4):
    lines, i, idx = [], 0, 1
    while i < len(words):
        chunk = words[i:i + words_per_line]
        if not chunk:
            break
        ls = max(0, chunk[0]["start"] - start_offset)
        le = max(ls + 0.1, chunk[-1]["end"] - start_offset)
        text = " ".join(w["word"] for w in chunk).strip()
        if text:
            lines += [str(idx), f"{srt_time(ls)} --> {srt_time(le)}", text, ""]
            idx += 1
        i += words_per_line
    with open(output_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))


def srt_time(s):
    h, m = int(s // 3600), int((s % 3600) // 60)
    sec, ms = int(s % 60), int((s % 1) * 1000)
    return f"{h:02d}:{m:02d}:{sec:02d},{ms:03d}"
