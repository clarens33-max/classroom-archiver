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
from flask import (
    Flask, render_template, send_file, abort, url_for,
    request, Response, stream_with_context, redirect, g,
)

app = Flask(__name__)

BASE_DIR = Path(__file__).parent
OUTPUT_DIR = BASE_DIR / "output"
AUDIO_EXTS = {".mp3", ".mp4", ".m4a", ".wav", ".aac", ".ogg", ".webm"}

# ── Course configuration ───────────────────────────────────────────────────────

COURSE_CONFIGS = {
    "AI_Solutions_Architecture": {
        "slug": "AISA",
        "display_name": "AI Solutions Architecture",
        "subtitle": "with Toby Fotherby",
        "instructor": "Toby Fotherby",
        "exercises_repo": "https://github.com/clarens33-max/elvtr-ai-solution-architect",
        "notebooklm_url": "https://notebooklm.google.com/notebook/06267860-45a2-4fc3-91e5-1277cf175396",
        "assignment_to_lesson": {1: 1, 2: 2, 3: 3, 4: 5, 5: 7, 6: 9, 7: 12, 8: 14},
        "color": "#1d4ed8",
        "icon": "bi-diagram-3",
        "description": "16-lesson programme covering AI solution architecture, from ML fundamentals to deploying production GenAI systems.",
    },
    "Chief_AI_Officer": {
        "slug": "CAIO",
        "display_name": "Chief AI Officer",
        "subtitle": "with Gule Sheikh",
        "instructor": "Gule Sheikh",
        "exercises_repo": None,
        "notebooklm_url": "https://notebooklm.google.com/notebook/adfecd54-ec5b-4549-bd05-fddb6195db1c",
        "assignment_to_lesson": {1: 2, 2: 4, 3: 8, 4: 10},
        "lesson_subtitles": {13: "13 AI and ESG Frameworks", 14: "14 CAIO Role and Course Wrap Up"},
        "color": "#7c3aed",
        "icon": "bi-person-badge",
        "description": "Strategic AI leadership programme for executives driving AI transformation across organisations.",
    },
}

# short slug → folder keyword (built from COURSE_CONFIGS)
SLUG_TO_KEY = {cfg["slug"]: key for key, cfg in COURSE_CONFIGS.items()}

EXCLUDED_DOMAINS = {"zoom.us", "vimeo.com", "player.vimeo.com"}


def get_course_config(folder_name):
    for key, cfg in COURSE_CONFIGS.items():
        if key in folder_name:
            return cfg
    return {
        "slug": folder_name,
        "display_name": nice_name(folder_name),
        "subtitle": "",
        "instructor": "Instructor",
        "exercises_repo": None,
        "assignment_to_lesson": {},
        "color": "#374151",
        "icon": "bi-book",
        "description": "",
    }


def slug_to_course_dir(slug):
    """Resolve a short slug (AISA/CAIO) or full folder name to a course Path."""
    key = SLUG_TO_KEY.get(slug.upper())
    if key:
        # find the output folder whose name contains the key
        for d in get_all_course_dirs():
            if key in d.name:
                return d
    # fallback: treat slug as literal folder name
    d = OUTPUT_DIR / slug
    return d if d.is_dir() else None


# ── Template filter: format transcript text into HTML speaker turns ───────────

@app.template_filter("format_transcript")
def format_transcript_filter(text):
    if not text:
        return markupsafe.Markup("")
    instructor = getattr(g, "speaker_a_name", "Instructor")
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
                speaker = instructor
            parts.append(
                f'<div class="tr-turn">'
                f'<span class="tr-speaker">{markupsafe.escape(speaker)}</span>'
                f'<span class="tr-text">{markupsafe.escape(m.group(2))}</span>'
                f"</div>"
            )
        else:
            parts.append(f'<div class="tr-line">{markupsafe.escape(s)}</div>')
    return markupsafe.Markup("\n".join(parts))


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
    try:
        p = (OUTPUT_DIR / rel_path).resolve()
        p.relative_to(OUTPUT_DIR.resolve())
        return p
    except (ValueError, OSError):
        return None


def nice_name(raw):
    s = raw.replace("_", " ")
    s = re.sub(r"\([Pp]assword[^)]*\)", "", s)
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


def parse_urls_text(content):
    links = []
    if not content:
        return links
    for line in content.splitlines():
        if "\u2192" in line:
            left, _, right = line.partition("\u2192")
            url = right.strip()
            if url.startswith("http") and not any(d in url for d in EXCLUDED_DOMAINS):
                links.append({"title": left.strip() or url, "url": url})
    return links


def scan_transcripts(folder):
    """Return the most recent transcript artefacts, checking gdrive_map for missing files."""
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
            else:
                # Check gdrive_map (Railway: summary.pdf and concept_map.png not in git)
                gdrive_rel = str(sub.relative_to(OUTPUT_DIR)).replace("\\", "/") + "/" + fname
                if gdrive_rel in _gdrive_map:
                    entry[key] = gdrive_rel
        if entry:
            results.append(entry)
    return results[-1:] if results else []


def scan_files(folder, *, skip_dirs=None):
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


def gdrive_files_for(folder_rel):
    """Return file entries from gdrive_map for all files under a folder (relative to OUTPUT_DIR)."""
    prefix = str(folder_rel).replace("\\", "/").rstrip("/") + "/"
    files = []
    for gdrive_path in _gdrive_map:
        if gdrive_path.startswith(prefix):
            fname = gdrive_path.split("/")[-1]
            ext = Path(fname).suffix.lower().lstrip(".")
            files.append({"name": fname, "path": gdrive_path, "ext": ext, "size": ""})
    return files


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
    return _gdrive_map.get(rel_path.replace("\\", "/"))


def gdrive_view_url(fid: str, ext: str = "") -> str:
    if ext.lower() == ".pdf":
        return f"https://drive.google.com/file/d/{fid}/preview"
    if ext.lower() in (".png", ".jpg", ".jpeg", ".gif", ".webp"):
        return f"https://lh3.googleusercontent.com/d/{fid}"
    return f"https://drive.google.com/file/d/{fid}/view"


def gdrive_download_url(fid: str) -> str:
    return f"https://drive.google.com/uc?export=download&id={fid}"


# ── Data scanner ──────────────────────────────────────────────────────────────

def get_all_course_dirs():
    if not OUTPUT_DIR.exists():
        return []
    return sorted(d for d in OUTPUT_DIR.iterdir() if d.is_dir())


def build_data(course_dir, config):
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
            skip_keywords = ("feedback", "evaluation", "quiz")
            if any(kw in title.lower() for kw in skip_keywords):
                continue
            url_links = parse_urls_text(read_text(folder / "resources_urls.txt"))
            files = [f for f in scan_files(folder) if f["ext"] not in ("txt",)]
            if not files:
                files = [f for f in gdrive_files_for(folder.relative_to(OUTPUT_DIR)) if f["ext"] not in ("txt",)]
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

            if is_link_folder:
                if lesson_num:
                    lessons[lesson_num]["resource_links"].extend(url_links)
                continue

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
                files = [f for f in scan_files(folder) if f["ext"] not in ("txt",)]
                if not files:
                    files = [f for f in gdrive_files_for(folder.relative_to(OUTPUT_DIR)) if f["ext"] not in ("txt",)]
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
                if not pdfs:
                    pdfs = [f for f in gdrive_files_for(folder.relative_to(OUTPUT_DIR)) if f["ext"] == "pdf"]
                if pdfs:
                    pdf_stem = pdfs[0]["name"].replace(".pdf", "")
                    sub = re.sub(r"^(Lesson_\d+\._?|Welcome_Lesson\._?|\d+\._?)", "", pdf_stem)
                    lessons[target]["subtitle"] = nice_name(sub)
                    lessons[target]["slides_pdf"] = pdfs[0]["path"]

            elif "Resources" in name or "Resource" in name:
                files = scan_files(folder)
                if not files:
                    files = gdrive_files_for(folder.relative_to(OUTPUT_DIR))
                lessons[target]["resource_files"].extend(f for f in files if f["ext"] not in ("txt",))
                lessons[target]["resource_links"].extend(url_links)

            elif "Recording" in name:
                lessons[target]["transcripts"].extend(scan_transcripts(folder))

            else:
                files = scan_files(folder)
                if not files:
                    files = gdrive_files_for(folder.relative_to(OUTPUT_DIR))
                lessons[target]["resource_files"].extend(f for f in files if f["ext"] not in ("txt",))
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

    # ── Second pass: fill gaps from gdrive_map for folders absent on Railway ──
    SLIDE_FOLDER_RE = re.compile(r"M\d+_((?:Lesson_(\d+)|Welcome_Lesson|Class_(\d+)).*_Slides)")
    RESOURCE_FOLDER_RE = re.compile(r"M\d+_((?:Lesson_(\d+)|Welcome_Lesson|Class_(\d+)).*_Resources?)")
    course_prefix = course_dir.name + "/"

    for gdrive_path in _gdrive_map:
        if not gdrive_path.startswith(course_prefix):
            continue
        rel_to_course = gdrive_path[len(course_prefix):]
        parts = rel_to_course.split("/")
        folder_name = parts[0]
        fname = parts[-1]
        ext = Path(fname).suffix.lower().lstrip(".")
        file_entry = {"name": fname, "path": gdrive_path, "ext": ext, "size": ""}

        ms = SLIDE_FOLDER_RE.search(folder_name)
        if ms:
            lesson_num = int(ms.group(2) or ms.group(3) or 0)
            if lessons[lesson_num]["slides_pdf"] is None and ext == "pdf":
                pdf_stem = fname.replace(".pdf", "")
                sub = re.sub(r"^(Lesson_\d+\._?|Welcome_Lesson\._?|\d+\._?)", "", pdf_stem)
                lessons[lesson_num]["number"] = lesson_num
                lessons[lesson_num]["subtitle"] = nice_name(sub)
                lessons[lesson_num]["slides_pdf"] = gdrive_path
            continue

        mr = RESOURCE_FOLDER_RE.search(folder_name)
        if mr:
            lesson_num = int(mr.group(2) or mr.group(3) or 0)
            if ext not in ("txt",) and file_entry not in lessons[lesson_num]["resource_files"]:
                lessons[lesson_num]["number"] = lesson_num
                lessons[lesson_num]["resource_files"].append(file_entry)
            continue

    # Apply manual subtitle overrides (for lessons whose slides weren't downloadable)
    for num, sub in config.get("lesson_subtitles", {}).items():
        if not lessons[num]["subtitle"]:
            lessons[num]["subtitle"] = sub

    # Sort and label
    sorted_lessons = sorted(
        (v for v in lessons.values() if v["number"] is not None),
        key=lambda x: x["number"],
    )
    for les in sorted_lessons:
        les["display_title"] = "Welcome Lesson" if les["number"] == 0 else f"Lesson {les['number']}"

    assignments.sort(key=lambda x: x["number"])

    assignment_to_lesson = config.get("assignment_to_lesson", {})
    lesson_by_num = {les["number"]: les for les in sorted_lessons}
    for a in assignments:
        m = re.search(r"[Aa]ssignment\s*#?\s*(\d+)", a["title"])
        lesson_num = assignment_to_lesson.get(int(m.group(1))) if m else None
        if lesson_num and lesson_num in lesson_by_num:
            lesson_by_num[lesson_num]["assignment"] = a
            continue
        special.append({
            "folder": a["folder"],
            "title": a["title"],
            "files": a["files"],
            "links": a["links"],
            "description": a.get("description"),
        })

    return {
        "slug": course_dir.name,
        "course_name": config["display_name"],
        "course_subtitle": config.get("subtitle", ""),
        "instructor": config.get("instructor", ""),
        "lessons": sorted_lessons,
        "office_hours": office_hours_items,
        "assignments": assignments,
        "special": special,
        "exercises_repo": config.get("exercises_repo"),
        "notebooklm_url": config.get("notebooklm_url"),
    }


_cache = {}  # slug → data dict


def get_course_data(slug):
    slug = slug.upper() if slug.upper() in SLUG_TO_KEY else slug
    if slug not in _cache:
        course_dir = slug_to_course_dir(slug)
        if not course_dir:
            return None
        config = get_course_config(course_dir.name)
        config = dict(config, slug=slug)  # ensure short slug propagates
        _cache[slug] = build_data(course_dir, config)
    return _cache[slug]


# ── AI Chat — per-course RAG with BM25 ───────────────────────────────────────

CHUNK_SIZE = 1500
CHUNK_OVERLAP = 200
TOP_K = 8

_rag_indices = {}  # slug → index dict


def _tokenize(text):
    return re.findall(r"\w+", text.lower())


def build_rag_index(slug):
    slug = slug.upper() if slug.upper() in SLUG_TO_KEY else slug
    if slug in _rag_indices:
        return _rag_indices[slug]

    course_dir = slug_to_course_dir(slug)
    if not course_dir:
        _rag_indices[slug] = None
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
        start = 0
        while start < len(text):
            end = start + CHUNK_SIZE
            chunks.append(text[start:end])
            labels.append(label)
            if end >= len(text):
                break
            start = end - CHUNK_OVERLAP

    if not chunks:
        _rag_indices[slug] = None
        return None

    tokenized = [_tokenize(c) for c in chunks]
    _rag_indices[slug] = {
        "chunks": chunks,
        "labels": labels,
        "bm25": BM25Okapi(tokenized),
        "count": len(set(labels)),
    }
    return _rag_indices[slug]


def retrieve_chunks(query, slug):
    idx = build_rag_index(slug)
    if not idx:
        return []
    scores = idx["bm25"].get_scores(_tokenize(query))
    top_indices = sorted(range(len(scores)), key=lambda i: scores[i], reverse=True)[:TOP_K]
    return [(idx["labels"][i], idx["chunks"][i]) for i in top_indices]


def build_system_prompt(query, slug):
    config = get_course_config(slug)
    idx = build_rag_index(slug)
    count = idx["count"] if idx else 0
    relevant = retrieve_chunks(query, slug)

    if relevant:
        context_parts = [f"## {label}\n\n{chunk}" for label, chunk in relevant]
        context_block = "RELEVANT TRANSCRIPT EXCERPTS:\n\n" + "\n\n---\n\n".join(context_parts)
    else:
        context_block = "No transcripts are available."

    return (
        f"You are an AI study assistant helping Clara review the archived "
        f"\"{config['display_name']}\" course taught by {config.get('instructor', 'the instructor')}. "
        f"The course has {count} recorded sessions.\n\n"
        f"Answer questions accurately based on the excerpts below, cite which lesson or session "
        f"the information comes from, and highlight the instructor's key points and examples. "
        f"If the answer isn't in the excerpts, say so clearly.\n\n"
        f"{context_block}"
    )


# ── Routes ────────────────────────────────────────────────────────────────────

@app.route("/")
def index():
    """Course picker — landing page."""
    courses = []
    for course_dir in get_all_course_dirs():
        config = get_course_config(course_dir.name)
        slug = config.get("slug", course_dir.name)
        data = get_course_data(slug)
        if not data:
            continue
        courses.append({
            "slug": slug,
            "display_name": config["display_name"],
            "subtitle": config.get("subtitle", ""),
            "description": config.get("description", ""),
            "color": config.get("color", "#374151"),
            "icon": config.get("icon", "bi-book"),
            "lesson_count": len(data["lessons"]),
            "transcript_count": sum(1 for l in data["lessons"] if l["transcripts"]),
            "office_hours_count": len(data["office_hours"]),
        })
    return render_template("picker.html", courses=courses)


@app.route("/<slug>/")
@app.route("/<slug>")
def course_index(slug):
    data = get_course_data(slug)
    if not data:
        abort(404)
    return render_template("index.html", **data)


@app.route("/<slug>/lesson/<int:num>")
def course_lesson(slug, num):
    data = get_course_data(slug)
    if not data:
        abort(404)
    les = next((l for l in data["lessons"] if l["number"] == num), None)
    if not les:
        abort(404)
    g.speaker_a_name = data.get("instructor", "Instructor")
    return render_template("lesson.html", lesson=les, **data)


@app.route("/<slug>/office-hours")
def course_office_hours(slug):
    data = get_course_data(slug)
    if not data:
        abort(404)
    return render_template("office_hours.html", **data)


@app.route("/<slug>/special")
def course_special(slug):
    data = get_course_data(slug)
    if not data:
        abort(404)
    return render_template("special.html", **data)


@app.route("/<slug>/assignments")
def course_assignments(slug):
    data = get_course_data(slug)
    if not data:
        abort(404)
    return render_template("assignments.html", **data)


@app.route("/<slug>/chat")
def course_chat(slug):
    data = get_course_data(slug)
    if not data:
        abort(404)
    idx = build_rag_index(slug)
    count = idx["count"] if idx else 0
    return render_template("chat.html", transcript_count=count, **data)


@app.route("/<slug>/chat/stream", methods=["POST"])
def course_chat_stream(slug):
    if not (os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_KEY")):
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

    query = next((m["content"] for m in reversed(messages) if m.get("role") == "user"), "")
    system = build_system_prompt(query, slug)

    def generate():
        try:
            client = anthropic.Anthropic(
                api_key=os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_KEY")
            )
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


@app.route("/files/<path:rel>")
def serve_file(rel):
    fid = gdrive_id(rel)
    if fid:
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
        return redirect(gdrive_download_url(fid))
    p = safe_resolve(rel)
    if not p or not p.exists() or is_audio(p):
        abort(404)
    return send_file(str(p), as_attachment=True)


@app.route("/transcript/<path:rel>")
def view_transcript(rel):
    slug = rel.split("/")[0]
    data = get_course_data(slug) or {}
    g.speaker_a_name = data.get("instructor", "Instructor")
    p = safe_resolve(rel)
    if not p or not p.exists():
        abort(404)
    content = read_text(p)
    return render_template("transcript.html", content=content, filepath=rel, **data)


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
