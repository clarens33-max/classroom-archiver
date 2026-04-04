# CLAUDE.md — Classroom Archiver Project

## What this project does

Clara is a Director of AI & Innovation enrolled in a Google Classroom course called
**"AI Solutions Architecture"** (16 lessons). The course is expiring and will become
inaccessible. This project archives everything and serves it through a web portal.

There are three parts:

1. **`classroom_archive.py`** — archives Google Classroom content
2. **`classroom_transcribe.py`** — transcribes Vimeo recordings
3. **`app.py`** — Flask web portal deployed on Railway

The two archive scripts work in sequence:

1. **`classroom_archive.py`** — connects to Google Classroom via API, loops through
   every lesson, exports slides as PPTX, downloads all resources, and detects Vimeo
   recordings by parsing the material descriptions (passwords are embedded in the
   description text, e.g. "Password: AISA26/11"). Produces a `_vimeo_queue.json`.

2. **`classroom_transcribe.py`** — reads `_vimeo_queue.json`, downloads each Vimeo
   video as audio using yt-dlp, transcribes with Whisper, and generates a summary PDF
   with a concept map using the Claude API.

## Project structure

```
classroom-archiver/
  classroom_archive.py       ← run first
  classroom_transcribe.py    ← run second
  credentials.json           ← Google OAuth (Clara provides this)
  token.json                 ← auto-generated on first auth run
  CLAUDE.md                  ← this file
  google_cloud_setup.md      ← setup guide for Clara
  output/                    ← all archived content goes here
    <CourseName>/
      M01_<Title>/           ← course materials
        *.pptx
        *_links.txt
        resources/
        resources_urls.txt
      A01_<Title>/           ← assignments
        description.txt
        resources/
      _vimeo_queue.json
      <LessonFolder>/
        transcripts/
          <Video>_TIMESTAMP/
            transcript.txt
            concept_map.png
            summary.pdf
```

## Environment variables required

```
ANTHROPIC_API_KEY   ← for generating summary PDFs (transcripts work without it)
```

## How to run this project

### Step 1 — Install dependencies (once only)

```bash
pip install google-auth google-auth-oauthlib google-api-python-client \
            python-pptx Pillow requests \
            openai-whisper yt-dlp \
            anthropic reportlab networkx matplotlib
```

ffmpeg must also be installed (required by Whisper and yt-dlp):
```bash
choco install ffmpeg    # Windows
brew install ffmpeg     # macOS
```

### Step 2 — First-time Google auth (once only)

Clara must have placed `credentials.json` in this folder first (see google_cloud_setup.md).
Then run:

```bash
python classroom_archive.py
```

A browser window will open asking Clara to sign in and grant access to Classroom, Drive,
and Slides. After approval, `token.json` is created and reused automatically from then on.

### Step 3 — Archive the course

```bash
python classroom_archive.py
```

The script lists all active courses and asks Clara to pick one by number.
After selection it runs fully automatically. Expect it to take 5–20 minutes
depending on the number of slide decks and files.

If a slide deck fails (e.g. permission error), it logs the error and continues.
The script is safe to re-run — existing files are skipped.

### Step 4 — Transcribe Vimeo recordings

```bash
python classroom_transcribe.py
```

Runs automatically. For each video it:
- Downloads audio (~1–2 min per video depending on length)
- Transcribes with Whisper medium model (~2–5 min per video)
- Generates summary PDF via Claude API (~30 seconds)

The script is safe to re-run — already-transcribed videos are skipped.

### Step 5 — Run the web portal locally

```bash
pip install flask gunicorn
python app.py
```

Open http://localhost:5000. The portal reads from `output/` automatically.

### Step 6 — Deploy to Railway

1. Create a new Railway project and connect this GitHub repo.
2. Set the start command to `gunicorn app:app --bind 0.0.0.0:$PORT --workers 2 --timeout 60`
   (or Railway will detect the `Procfile` automatically).
3. Commit the `output/` folder to git **before pushing** — audio files are excluded by `.gitignore`.
4. No environment variables are required for the portal itself.

## Web portal (`app.py`)

- Scans `output/` at startup and caches the course structure in memory.
- Groups M-folders by lesson number extracted from folder names.
- Serves PDFs, PNGs, and text files inline or as downloads.
- **Audio files are never served** (blocked at the route level, excluded from git).
- Re-start the app (or redeploy) to pick up new files after re-running the archive scripts.

```
Routes:
  /                     → lesson grid overview
  /lesson/<N>           → lesson detail (slides, transcript, resources tabs)
  /office-hours         → all office hours sessions
  /assignments          → all assignments
  /special              → course programme, FAQ, pre-course materials
  /files/<path>         → serve file inline (PDF viewer, image)
  /download/<path>      → force-download a file
  /transcript/<path>    → view transcript or chat log as HTML
```

## Troubleshooting

| Problem | Fix |
|---|---|
| `credentials.json not found` | Clara needs to complete google_cloud_setup.md |
| `insufficient permissions` on a slide deck | That deck is not shared with Clara's account. Note it, skip, move on. |
| `HttpError 403` | Same as above or Drive quota issue. Re-run after a few minutes. |
| `HttpError 429` (rate limit) | Wait 60 seconds, re-run. |
| yt-dlp download fails | Check the Vimeo password was captured correctly in `_vimeo_queue.json`. Edit it manually if needed. |
| Whisper runs out of memory | Change `WHISPER_MODEL = "base"` in classroom_transcribe.py for lower memory usage. |
| token.json error / expired | Delete `token.json` and re-run — browser auth will trigger again. |

## Course exercise repository

The course exercises were hosted on the instructor's GitHub, which will become inaccessible when the course expires.

- **Use this (Clara's fork):** https://github.com/clarens33-max/elvtr-ai-solution-architect
- ~~Original instructor repo (obsolete):~~ https://github.com/toby-fotherby/elvtr-ai-solution-architect

## Key implementation notes for Claude Code

- **Passwords are auto-extracted** from material description text using a regex that
  matches "Password: XXXXX" patterns. Check `_vimeo_queue.json` after running
  classroom_archive.py to verify they were captured correctly before transcribing.

- **The Slides API thumbnail endpoint** requires the OAuth token to be passed as a
  Bearer header on a direct HTTP request — it cannot go through the Python client library.

- **Re-run safety**: both scripts check for existing output files and skip them.
  This means if something fails partway through, re-running picks up where it left off.

- **Vimeo queue editing**: if a password was not captured correctly (e.g. it used an
  unusual format), edit `_vimeo_queue.json` directly and re-run classroom_transcribe.py.
  Only the videos with missing/wrong passwords need updating.
