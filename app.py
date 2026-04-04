"""
Classroom Archive Portal
Flask web app serving course content from the output/ directory.
Deploy on Railway — reads PORT from environment.
"""
import os
import re
import json
from pathlib import Path
from collections import defaultdict
import markupsafe
import anthropic
from rank_bm25 import BM25Okapi
from flask import Flask, render_template, send_file, abort, url_for, request, Response, stream_with_context

app = Flask(__name__)


# ── Template filter: format transcript text into HTML speaker turns ───────────

@app.template_filter("format_transcript")
def format_transcript_filter(text):
    if not text:
        return markupsafe.Markup("")
    parts = []
    for line in text.split("\n"):
        s = line.strip()
        if not s:
            parts.append('<div class="tr-gap"></div>')
            continue
        if s.startswith("#"):
            parts.append(f'<div class="tr-meta">{markupsafe.escape(s)}</div>')
            continue
        m = re.match(r"^(Speaker\s+\w+):\s*(.*)", s)
        if m:
            speaker = m.group(1)
            if speaker.strip().upper() == "SPEAKER A":
                speaker = "Toby Fotherby"
            parts.append(
                f'<div class="tr-turn">'
                f'<span class="tr-speaker">{markupsafe.escape(speaker)}</span>'
                f'<span class="tr-text">{markupsafe.escape(m.group(2))}</span>'
                f"</div>"
            )
        else:
            parts.append(f'<div class="tr-line">{markupsafe.escape(s)}</div>')
    return markupsafe.Markup("\n".join(parts))

BASE_DIR = Path(__file__).parent
OUTPUT_DIR = BASE_DIR / "output"
AUDIO_EXTS = {".mp3", ".mp4", ".m4a", ".wav", ".aac", ".ogg", ".webm"}


# ── Utilities ─────────────────────────────────────────────────────────────────

def is_audio(path):
    return Path(path).suffix.lower() in AUDIO_EXTS


def read_text(path):
    try:
        return Path(path).read_text(encoding="utf-8", errors="replace")
    except Exception:
        return None


def human_size(n):
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.0f} {unit}"
        n /= 1024
    return f"{n:.1f} TB"


def safe_resolve(rel_path):
    """Return absolute Path only if safely inside OUTPUT_DIR."""
    try:
        p = (OUTPUT_DIR / rel_path).resolve()
        p.relative_to(OUTPUT_DIR.resolve())
        return p
    except (ValueError, OSError):
        return None


def nice_name(raw):
    """Convert raw folder/filename fragment to readable display text."""
    s = raw.replace("_", " ")
    s = re.sub(r"\([Pp]assword[^)]*\)", "", s)   # strip password hints
    s = re.sub(r"\s+", " ", s).strip(" .")
    return s


def extract_lesson_num(name):
    m = re.search(r"[Ll]esson[_\s\.]+(\d+)", name)
    if m:
        return int(m.group(1))
    m = re.search(r"[Cc]lass[_\s\.]+(\d+)", name)
    if m:
        return int(m.group(1))
    return None


def is_welcome(name):
    return bool(re.search(r"[Ww]elcome", name))


def is_office_hours(name):
    return bool(re.search(r"[Oo]ffice.?[Hh]ours", name, re.IGNORECASE))


EXCLUDED_DOMAINS = {"zoom.us", "vimeo.com", "player.vimeo.com"}


def parse_urls_text(content):
    """Parse resources_urls.txt → list of {title, url}. Zoom and Vimeo links are excluded."""
    links = []
    if not content:
        return links
    for line in content.splitlines():
        if "\u2192" in line:  # → character
            left, _, right = line.partition("\u2192")
            url = right.strip()
            if url.startswith("http") and not any(d in url for d in EXCLUDED_DOMAINS):
                links.append({"title": left.strip() or url, "url": url})
    return links


def scan_transcripts(folder):
    """Return the most recent transcript artefacts under folder/transcripts/."""
    results = []
    t_dir = Path(folder) / "transcripts"
    if not t_dir.exists():
        return results
    for sub in sorted(t_dir.iterdir()):
        if not sub.is_dir():
            continue
        entry = {}
        for fname, key in [
            ("transcript.txt", "transcript"),
            ("summary.pdf", "summary_pdf"),
            ("concept_map.png", "concept_map"),
        ]:
            fp = sub / fname
            if fp.exists():
                entry[key] = str(fp.relative_to(OUTPUT_DIR)).replace("\\", "/")
        if entry:
            results.append(entry)
    # Keep only the latest timestamp subfolder
    return results[-1:] if results else []


def scan_files(folder, *, skip_dirs=None):
    """Return list of file dicts for all non-audio files under folder."""
    skip_dirs = set(skip_dirs or [])
    files = []
    for root, dirs, fnames in os.walk(folder):
        dirs[:] = [d for d in dirs if d not in skip_dirs]
        for fn in sorted(fnames):
            fp = Path(root) / fn
            if is_audio(fp):
                continue
            rel = str(fp.relative_to(OUTPUT_DIR)).replace("\\", "/")
            files.append({
                "name": fn,
                "path": rel,
                "size": human_size(fp.stat().st_size),
                "ext": fp.suffix.lower().lstrip("."),
            })
    return files


# ── Data scanner ──────────────────────────────────────────────────────────────

def get_course_dir():
    dirs = [d for d in OUTPUT_DIR.iterdir() if d.is_dir()]
    return dirs[0] if dirs else None


def build_data():
    course_dir = get_course_dir()
    if not course_dir:
        return None

    def empty_lesson():
        return {
            "number": None,
            "display_title": "",
            "subtitle": "",
            "slides_pdf": None,
            "transcripts": [],
            "resource_links": [],
            "resource_files": [],
            "assignment": None,
        }

    lessons = defaultdict(empty_lesson)
    office_hours_items = []
    assignments = []
    special = []

    for folder in sorted(course_dir.iterdir()):
        name = folder.name
        if not folder.is_dir():
            continue

        # ── Assignments (A-folders) ──
        m = re.match(r"^A(\d+)_(.+)$", name)
        if m:
            num, raw_title = int(m.group(1)), m.group(2)
            title = nice_name(raw_title)
            # Skip feedback/evaluation forms
            skip_keywords = ("feedback", "evaluation", "quiz")
            if any(kw in title.lower() for kw in skip_keywords):
                continue
            url_links = parse_urls_text(read_text(folder / "resources_urls.txt"))
            files = [f for f in scan_files(folder) if f["ext"] not in ("txt",)]
            assignments.append({
                "number": num,
                "folder": name,
                "title": title,
                "description": read_text(folder / "description.txt"),
                "links": url_links,
                "files": files,
            })
            continue

        # ── Materials (M-folders) ──
        m = re.match(r"^M(\d+)_(.+)$", name)
        if m:
            raw_title = m.group(2)
            lesson_num = extract_lesson_num(name)
            is_welcome_lesson = is_welcome(name) and not lesson_num
            is_oh = is_office_hours(name)
            is_link_folder = bool(re.match(r"Link_to", raw_title))
            url_links = parse_urls_text(read_text(folder / "resources_urls.txt"))

            # Link-only folders: attach their URL to the lesson
            if is_link_folder:
                if lesson_num:
                    lessons[lesson_num]["resource_links"].extend(url_links)
                continue

            # Office Hours M-folders
            if is_oh:
                t = scan_transcripts(folder)
                office_hours_items.append({
                    "folder": name,
                    "title": nice_name(raw_title),
                    "transcripts": t,
                    "links": url_links,
                })
                continue

            target = lesson_num if lesson_num else (0 if is_welcome_lesson else None)

            if target is None:
                # Special / pre-course content
                files = [f for f in scan_files(folder) if f["ext"] not in ("txt",)]
                if files or url_links:
                    special.append({
                        "folder": name,
                        "title": nice_name(raw_title),
                        "files": files,
                        "links": url_links,
                    })
                continue

            lessons[target]["number"] = target

            if "Slides" in name:
                files = scan_files(folder, skip_dirs={"resources"})
                pdfs = [f for f in files if f["ext"] == "pdf"]
                if pdfs:
                    pdf_stem = pdfs[0]["name"].replace(".pdf", "")
                    sub = re.sub(r"^(Lesson_\d+\._?|Welcome_Lesson\._?|\d+\._?)", "", pdf_stem)
                    lessons[target]["subtitle"] = nice_name(sub)
                    lessons[target]["slides_pdf"] = pdfs[0]["path"]

            elif "Resources" in name or "Resource" in name:
                files = scan_files(folder)
                lessons[target]["resource_files"].extend(
                    f for f in files if f["ext"] not in ("txt",)
                )
                lessons[target]["resource_links"].extend(url_links)

            elif "Recording" in name:
                lessons[target]["transcripts"].extend(scan_transcripts(folder))

            else:
                files = scan_files(folder)
                lessons[target]["resource_files"].extend(
                    f for f in files if f["ext"] not in ("txt",)
                )
                lessons[target]["resource_links"].extend(url_links)
            continue

        # ── Standalone Lesson_N._Recording_ folders ──
        m = re.match(r"^Lesson_(\d+)\._Recording_", name)
        if m:
            num = int(m.group(1))
            lessons[num]["number"] = num
            lessons[num]["transcripts"].extend(scan_transcripts(folder))
            continue

        # ── Standalone Office_Hours._Recording_ folders ──
        if re.match(r"^Office_Hours\._Recording_", name):
            t = scan_transcripts(folder)
            password = re.search(r"Password__(\w+)", name)
            pw = password.group(1) if password else ""
            merged = False
            if pw:
                for oh in office_hours_items:
                    if pw in oh["folder"]:
                        oh["transcripts"].extend(t)
                        merged = True
                        break
            if not merged:
                office_hours_items.append({
                    "folder": name,
                    "title": nice_name(name),
                    "transcripts": t,
                    "links": [],
                })
            continue

    # Sort and label lessons
    sorted_lessons = sorted(
        (v for v in lessons.values() if v["number"] is not None),
        key=lambda x: x["number"],
    )
    for les in sorted_lessons:
        les["display_title"] = "Welcome Lesson" if les["number"] == 0 else f"Lesson {les['number']}"

    assignments.sort(key=lambda x: x["number"])

    # Hardcoded assignment# → lesson number mapping
    ASSIGNMENT_TO_LESSON = {1: 1, 2: 2, 3: 3, 4: 5, 5: 7, 6: 9, 7: 12, 8: 14}
    lesson_by_num = {les["number"]: les for les in sorted_lessons}
    for a in assignments:
        m = re.search(r"#\s*(\d+)", a["title"])
        lesson_num = ASSIGNMENT_TO_LESSON.get(int(m.group(1))) if m else None
        if lesson_num and lesson_num in lesson_by_num:
            lesson_by_num[lesson_num]["assignment"] = a
            continue
        # Unmapped: add to special materials
        special.append({
            "folder": a["folder"],
            "title": a["title"],
            "files": a["files"],
            "links": a["links"],
            "description": a.get("description"),
        })

    return {
        "course_name": "AI Solutions Architecture",
        "lessons": sorted_lessons,
        "office_hours": office_hours_items,
        "special": special,
    }


_cache = None


def get_data():
    global _cache
    if _cache is None:
        _cache = build_data()
    return _cache


# ── Google Drive file map ─────────────────────────────────────────────────────

_gdrive_map: dict = {}

def _load_gdrive_map():
    global _gdrive_map
    p = BASE_DIR / "gdrive_map.json"
    if p.exists():
        try:
            _gdrive_map = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            _gdrive_map = {}

_load_gdrive_map()


def gdrive_id(rel_path: str):
    """Return Drive file ID for a relative path, or None if not in map."""
    return _gdrive_map.get(rel_path.replace("\\", "/"))


def gdrive_view_url(fid: str, ext: str = "") -> str:
    """URL for opening/embedding a Drive file."""
    if ext.lower() == ".pdf":
        return f"https://drive.google.com/file/d/{fid}/preview"
    if ext.lower() in (".png", ".jpg", ".jpeg", ".gif", ".webp"):
        return f"https://lh3.googleusercontent.com/d/{fid}"
    return f"https://drive.google.com/file/d/{fid}/view"


def gdrive_download_url(fid: str) -> str:
    return f"https://drive.google.com/uc?export=download&id={fid}"


# ── Routes ────────────────────────────────────────────────────────────────────

@app.route("/")
def index():
    return render_template("index.html", **get_data())


@app.route("/lesson/<int:num>")
def lesson(num):
    data = get_data()
    les = next((l for l in data["lessons"] if l["number"] == num), None)
    if not les:
        abort(404)
    return render_template("lesson.html", lesson=les, **data)


@app.route("/office-hours")
def office_hours():
    return render_template("office_hours.html", **get_data())


@app.route("/special")
def special_materials():
    return render_template("special.html", **get_data())


@app.route("/files/<path:rel>")
def serve_file(rel):
    fid = gdrive_id(rel)
    if fid:
        from flask import redirect
        ext = Path(rel).suffix.lower()
        return redirect(gdrive_view_url(fid, ext))
    p = safe_resolve(rel)
    if not p or not p.exists() or is_audio(p):
        abort(404)
    return send_file(str(p))


@app.route("/download/<path:rel>")
def download_file(rel):
    fid = gdrive_id(rel)
    if fid:
        from flask import redirect
        return redirect(gdrive_download_url(fid))
    p = safe_resolve(rel)
    if not p or not p.exists() or is_audio(p):
        abort(404)
    return send_file(str(p), as_attachment=True)


@app.route("/transcript/<path:rel>")
def view_transcript(rel):
    # Transcripts are plain .txt — they stay in git, always read locally
    p = safe_resolve(rel)
    if not p or not p.exists():
        abort(404)
    content = read_text(p)
    data = get_data()
    return render_template("transcript.html", content=content, filepath=rel, **data)


# ── AI Chat (RAG with BM25) ───────────────────────────────────────────────────

CHUNK_SIZE = 1500       # characters per chunk
CHUNK_OVERLAP = 200     # overlap between consecutive chunks
TOP_K = 8               # chunks to retrieve per query

_rag_index = None       # {"chunks": [...], "labels": [...], "bm25": BM25Okapi}


def _tokenize(text):
    return re.findall(r"\w+", text.lower())


def build_rag_index():
    """Chunk all transcripts and build a BM25 index. Called once at startup."""
    global _rag_index
    if _rag_index is not None:
        return _rag_index

    course_dir = get_course_dir()
    if not course_dir:
        _rag_index = None
        return None

    chunks, labels = [], []
    for folder in sorted(course_dir.iterdir()):
        if not folder.is_dir():
            continue
        t_dir = folder / "transcripts"
        if not t_dir.exists():
            continue
        subs = sorted(s for s in t_dir.iterdir() if s.is_dir())
        if not subs:
            continue
        tf = subs[-1] / "transcript.txt"
        if not tf.exists():
            continue
        label = nice_name(re.sub(r"\([Pp]assword[^)]*\)", "", folder.name))
        text = read_text(tf) or ""
        # Slide through the transcript in overlapping windows
        start = 0
        while start < len(text):
            end = start + CHUNK_SIZE
            chunks.append(text[start:end])
            labels.append(label)
            if end >= len(text):
                break
            start = end - CHUNK_OVERLAP

    if not chunks:
        _rag_index = None
        return None

    tokenized = [_tokenize(c) for c in chunks]
    _rag_index = {"chunks": chunks, "labels": labels, "bm25": BM25Okapi(tokenized), "count": len(set(labels))}
    return _rag_index


def retrieve_chunks(query):
    """Return the top-K most relevant (label, chunk) pairs for a query string."""
    idx = build_rag_index()
    if not idx:
        return []
    scores = idx["bm25"].get_scores(_tokenize(query))
    top_indices = sorted(range(len(scores)), key=lambda i: scores[i], reverse=True)[:TOP_K]
    return [(idx["labels"][i], idx["chunks"][i]) for i in top_indices]


def build_system_prompt(query):
    idx = build_rag_index()
    count = idx["count"] if idx else 0
    relevant = retrieve_chunks(query)

    if relevant:
        context_parts = []
        for label, chunk in relevant:
            context_parts.append(f"## {label}\n\n{chunk}")
        context = "\n\n---\n\n".join(context_parts)
        context_block = f"RELEVANT TRANSCRIPT EXCERPTS:\n\n{context}"
    else:
        context_block = "No transcripts are available."

    return (
        f"You are an AI study assistant helping Clara review the archived "
        f"\"AI Solutions Architecture\" course taught by Toby Fotherby. "
        f"The course has {count} recorded sessions.\n\n"
        f"Answer questions accurately based on the excerpts below, cite which lesson or session "
        f"the information comes from, and highlight Toby's key points and examples. "
        f"If the answer isn't in the excerpts, say so clearly.\n\n"
        f"{context_block}"
    )


@app.route("/chat")
def chat():
    idx = build_rag_index()
    count = idx["count"] if idx else 0
    return render_template("chat.html", transcript_count=count, **get_data())


@app.route("/chat/stream", methods=["POST"])
def chat_stream():
    if not os.environ.get("ANTHROPIC_API_KEY"):
        return Response(
            'data: {"error": "ANTHROPIC_API_KEY is not set."}\n\ndata: [DONE]\n\n',
            mimetype="text/event-stream",
        )

    body = request.get_json(silent=True) or {}
    messages = body.get("messages", [])
    if not messages:
        return Response(
            'data: {"error": "No messages provided."}\n\ndata: [DONE]\n\n',
            mimetype="text/event-stream",
        )

    # Use the latest user message as the retrieval query
    query = next((m["content"] for m in reversed(messages) if m.get("role") == "user"), "")
    system = build_system_prompt(query)

    def generate():
        try:
            client = anthropic.Anthropic()
            with client.messages.stream(
                model="claude-sonnet-4-6",
                max_tokens=2048,
                system=system,
                messages=messages,
            ) as stream:
                for text in stream.text_stream:
                    yield f"data: {json.dumps({'text': text})}\n\n"
        except anthropic.APIStatusError as e:
            yield f"data: {json.dumps({'error': f'API error: {e.message}'})}\n\n"
        except Exception as e:
            yield f"data: {json.dumps({'error': str(e)})}\n\n"
        yield "data: [DONE]\n\n"

    return Response(
        stream_with_context(generate()),
        mimetype="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
