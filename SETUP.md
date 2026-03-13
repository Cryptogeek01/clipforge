# ClipForge v2 — Setup & Deployment Guide
# =========================================
# Upload video → Claude picks best clips → Subtitles + silence removal
# + vertical crop + transitions → Download your clips
# =========================================

## WHAT YOU GET
A live web app at a real URL (e.g. clipforge-simon.up.railway.app)
Open it from any browser, any device, anywhere. No installations needed.

---

## OPTION A: DEPLOY TO RAILWAY (Recommended — Live URL, no setup)

### Step 1: Create a GitHub account
Go to https://github.com and sign up for a free account.

### Step 2: Create a new repository
1. Click the "+" icon (top right) → "New repository"
2. Name it: clipforge
3. Set it to Public
4. Click "Create repository"
5. You'll see a page with setup instructions — leave it open

### Step 3: Upload your files
1. On the repository page, click "uploading an existing file"
2. Drag and drop ALL the files from this zip:
   - main.py
   - requirements.txt
   - railway.toml
   - nixpacks.toml
   - Procfile
   - static/ folder (with index.html inside)
3. Click "Commit changes"

### Step 4: Create a Railway account
Go to https://railway.app and sign up (use your GitHub account — it's easier).

### Step 5: Deploy
1. In Railway dashboard, click "New Project"
2. Click "Deploy from GitHub repo"
3. Select your "clipforge" repository
4. Railway will detect the config and start building
5. Wait 3-5 minutes for the first build (it installs FFmpeg + Python packages)

### Step 6: Get your URL
1. In Railway, click on your project
2. Click "Settings" → "Networking" → "Generate Domain"
3. You'll get a URL like: clipforge.up.railway.app
4. Open it — ClipForge is live!

### Notes on Railway Free Tier:
- $5 free credits per month
- Each video processing job uses ~$0.02–0.10 of compute
- That's 50–250 videos per month for free
- Upgrade to $5/month for unlimited, always-on hosting

---

## OPTION B: RUN LOCALLY (Your own machine)

### Step 1: Install Python
- Windows: https://python.org/downloads → Download Python 3.11
  ⚠️ CHECK "Add Python to PATH" during install
- Mac: brew install python  (get brew at https://brew.sh)
- Linux: sudo apt install python3 python3-pip -y

### Step 2: Install FFmpeg
- Windows:
  1. Download from https://github.com/BtbN/FFmpeg-Builds/releases
     (get ffmpeg-master-latest-win64-gpl.zip)
  2. Extract → move folder to C:\ffmpeg
  3. Add C:\ffmpeg\bin to your system PATH
  4. Restart terminal → verify: ffmpeg -version
- Mac: brew install ffmpeg
- Linux: sudo apt install ffmpeg -y

### Step 3: Install Python packages
Open terminal in the clipforge2 folder and run:
  pip install -r requirements.txt

### Step 4: Run
  python -m uvicorn main:app --host 0.0.0.0 --port 8000

Open browser at: http://localhost:8000

---

## GETTING API KEYS

### Anthropic API Key (Required)
1. Go to https://console.anthropic.com
2. Sign up / log in
3. Go to "API Keys" → "Create Key"
4. Copy it (starts with sk-ant-)
5. Paste into ClipForge when processing

Cost: ~$0.01–0.05 per video (Claude reads the transcript, not the video file)

### Groq API Key (Optional — makes transcription 10x faster)
1. Go to https://console.groq.com
2. Sign up (free)
3. Go to "API Keys" → create one
4. Copy it (starts with gsk_)
5. Paste into ClipForge's optional Groq field

Groq's Whisper API is free tier, very fast.
Without it, transcription uses local Whisper (slower but free).

---

## HOW TO USE CLIPFORGE

1. Open the URL (Railway) or http://localhost:8000 (local)
2. Drop or click to upload your video
3. Choose settings:

   CLIP STYLE
   - Highlights: AI finds the most engaging, viral moments
   - Hook: Opens with a grabby statement
   - Story Arc: Complete mini-story with start/middle/end
   - Quote: Standalone soundbites
   - Educational: Clear, teachable moments
   - Emotional: Authentic, vulnerable moments

   FORMAT
   - Vertical 9:16 for TikTok, Instagram Reels, YouTube Shorts
   - Horizontal for YouTube, Twitter, LinkedIn

   CLIP COUNT & DURATION
   - 1–10 clips, 15–180 seconds each

   SUBTITLES
   - Word-by-Word: CapCut-style, one word pops at a time
   - Static Lines: 4 words per line
   - Position: Top / Middle / Bottom
   - Colour: White / Yellow / Neon Green

   ENHANCEMENTS
   - Remove Silence: Cuts dead air (makes it snappier)
   - Normalize Audio: Balances volume levels
   - Transition: Hard cut / Zoom cut / Fade in-out

   CONTENT BRIEF (the secret weapon)
   - Describe your client, their audience, and platform
   - Example: "Fintech brand targeting Nigerian 25–35 year olds,
     posting on LinkedIn. Tone: confident, direct, no fluff."
   - Claude uses this to pick clips for THAT specific context

4. Paste your Anthropic API key
5. Click "Forge Clips"
6. Wait (2–8 minutes depending on video length)
7. Download your clips — MP4, ready to post

---

## PROCESSING TIME ESTIMATES
- 5 min video → ~1-3 mins
- 15 min video → ~3-6 mins
- 30 min video → ~6-12 mins
- 60 min video → ~12-20 mins

Groq key cuts transcription time by ~10x.

---

## TROUBLESHOOTING

"ffmpeg not found":
→ FFmpeg not in PATH. Reinstall following Step 2 exactly, restart terminal.

Transcription stuck / very slow:
→ First run downloads Whisper model (~150MB). Wait for it.
→ Add a Groq key to skip local transcription entirely.

"Claude API 401":
→ Invalid or expired API key. Get a new one at console.anthropic.com.

"Claude API 429":
→ Rate limited. Wait 60 seconds and retry.

Railway build fails:
→ Make sure all files (including nixpacks.toml) were uploaded to GitHub.
→ Check Railway build logs for specific errors.

Clips download but have no subtitles:
→ The video may have had no speech. Try a video with talking.
→ Check that "Auto Subtitles" is checked.

---

## FILE STRUCTURE
clipforge2/
├── main.py              ← Full backend: pipeline, transcription, AI, rendering
├── requirements.txt     ← Python dependencies
├── railway.toml         ← Railway deployment config
├── nixpacks.toml        ← Tells Railway to install FFmpeg + Python
├── Procfile             ← Start command for Railway
├── static/
│   └── index.html       ← Complete frontend UI
├── uploads/             ← Temp storage (auto-cleaned)
└── outputs/             ← Your rendered clips live here

---

That's everything. The code is production-grade.
You own it. Extend it however you want.
