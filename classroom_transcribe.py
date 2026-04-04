"""
classroom_transcribe.py
────────────────────────────────────────────────────────────────────────────────
Reads _vimeo_queue.json produced by classroom_archive.py.
For each video: downloads audio with yt-dlp, transcribes with AssemblyAI
(with speaker diarization), then uses Claude to generate a concept-map
summary PDF.

Output (per video, inside the lesson folder):
  transcripts/
    <VideoTitle>_<YYYYMMDD_HHMMSS>/
      transcript.txt      ← labelled by speaker
      concept_map.png
      summary.pdf

Prerequisites:
  pip install assemblyai yt-dlp anthropic reportlab networkx matplotlib
  choco install ffmpeg   (Windows)
  Set ASSEMBLYAI_API_KEY and ANTHROPIC_API_KEY env vars.
────────────────────────────────────────────────────────────────────────────────
"""

import os
import io
import re
import sys
import json
import subprocess
from datetime import datetime
from pathlib import Path

# Allow emoji output on Windows CMD
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.stderr.reconfigure(encoding="utf-8", errors="replace")

import assemblyai as aai
import anthropic
import networkx as nx
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

from reportlab.lib.pagesizes import A4
from reportlab.lib import colors
from reportlab.lib.units import mm
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer,
    Image as RLImage, HRFlowable, PageBreak,
)

OUTPUT_DIR           = "output"
ANTHROPIC_API_KEY    = os.environ.get("ANTHROPIC_API_KEY", "")
ASSEMBLYAI_API_KEY   = os.environ.get("ASSEMBLYAI_API_KEY", "")

# ── HELPERS ───────────────────────────────────────────────────────────────────

def safe_name(s, max_len=60):
    s = re.sub(r'[\\/*?:"<>|]', "_", str(s))
    s = re.sub(r"\s+", "_", s.strip())
    return s[:max_len]


# ── DOWNLOAD ──────────────────────────────────────────────────────────────────

def download_audio(url, password, audio_path):
    cmd = ["yt-dlp", "-x", "--audio-format", "mp3", "-o", str(audio_path), url]
    if password:
        cmd += ["--video-password", password]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(result.stderr[:400])


# ── ASSEMBLYAI TRANSCRIPTION ──────────────────────────────────────────────────

def transcribe_audio(audio_path):
    """Upload to AssemblyAI and transcribe with speaker diarization."""
    aai.settings.api_key = ASSEMBLYAI_API_KEY
    config = aai.TranscriptionConfig(
        speaker_labels=True,
        speech_models=["universal-2"],
    )
    transcriber = aai.Transcriber(config=config)
    result = transcriber.transcribe(str(audio_path))
    if result.status == aai.TranscriptStatus.error:
        raise RuntimeError(result.error)

    # Format as labelled speaker turns
    lines = []
    for utt in result.utterances:
        lines.append(f"Speaker {utt.speaker}: {utt.text}")
    return "\n\n".join(lines)


# ── CLAUDE SUMMARY ────────────────────────────────────────────────────────────

def get_summary(transcript, title):
    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    prompt = f"""You are an expert learning designer summarising a lesson recording.
Lesson title: "{title}"

Respond ONLY with valid JSON (no markdown fences, no preamble):
{{
  "summary": "3-5 paragraph plain-text summary of the lesson content",
  "concepts": ["key concept 1", "key concept 2", "... up to 10"],
  "edges": [["concept A","concept B"], ["concept B","concept C"]],
  "takeaways": ["takeaway 1", "takeaway 2", "... up to 5"],
  "references": ["tool or framework mentioned", "..."]
}}

TRANSCRIPT:
{transcript[:12000]}"""

    resp = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY).messages.create(
        model="claude-opus-4-6",
        max_tokens=2000,
        messages=[{"role": "user", "content": prompt}],
    )
    raw = resp.content[0].text.strip().lstrip("```json").lstrip("```").rstrip("```").strip()
    return json.loads(raw)


# ── CONCEPT MAP ───────────────────────────────────────────────────────────────

def build_concept_map(concepts, edges, title, out_path):
    G = nx.Graph()
    G.add_nodes_from(concepts)
    for e in edges:
        if len(e) == 2 and e[0] in concepts and e[1] in concepts:
            G.add_edge(e[0], e[1])

    fig, ax = plt.subplots(figsize=(12, 7))
    ax.set_facecolor("#1a1a2e")
    fig.patch.set_facecolor("#1a1a2e")
    pos = nx.spring_layout(G, seed=42, k=2.5)
    nx.draw_networkx_edges(G, pos, ax=ax, edge_color="#4a90d9", alpha=0.6, width=1.5)
    nx.draw_networkx_nodes(G, pos, ax=ax, node_color="#e94560", node_size=900)
    nx.draw_networkx_labels(G, pos, ax=ax, font_color="white", font_size=8, font_weight="bold")
    ax.set_title(title, color="white", fontsize=11, pad=10)
    ax.axis("off")
    plt.tight_layout()
    plt.savefig(str(out_path), dpi=150, bbox_inches="tight", facecolor=fig.get_facecolor())
    plt.close()


# ── PDF ───────────────────────────────────────────────────────────────────────

def build_pdf(data, transcript, title, map_path, out_path):
    doc    = SimpleDocTemplate(str(out_path), pagesize=A4,
               leftMargin=20*mm, rightMargin=20*mm,
               topMargin=20*mm, bottomMargin=20*mm)
    styles = getSampleStyleSheet()
    H1 = ParagraphStyle("H1", parent=styles["Heading1"],
           fontSize=20, textColor=colors.HexColor("#1a1a2e"), spaceAfter=6)
    H2 = ParagraphStyle("H2", parent=styles["Heading2"],
           fontSize=13, textColor=colors.HexColor("#4a90d9"), spaceAfter=4)
    BD = ParagraphStyle("BD", parent=styles["Normal"],
           fontSize=10, leading=15, spaceAfter=6)
    BL = ParagraphStyle("BL", parent=styles["Normal"],
           fontSize=10, leading=14, leftIndent=12, spaceAfter=3)

    story = [
        Paragraph(title, H1),
        HRFlowable(width="100%", thickness=2, color=colors.HexColor("#4a90d9")),
        Spacer(1, 6*mm),
        Paragraph("Summary", H2),
        Paragraph(data["summary"], BD),
        Spacer(1, 4*mm),
        Paragraph("Key Takeaways", H2),
    ]
    for t in data.get("takeaways", []):
        story.append(Paragraph(f"• {t}", BL))

    refs = data.get("references", [])
    if refs:
        story += [Spacer(1, 4*mm), Paragraph("Tools & Frameworks Mentioned", H2)]
        for r in refs:
            story.append(Paragraph(f"• {r}", BL))

    if map_path.exists():
        story += [
            PageBreak(),
            Paragraph("Concept Map", H2),
            Spacer(1, 3*mm),
            RLImage(str(map_path), width=170*mm, height=170*mm * 7/12),
        ]

    story += [
        PageBreak(),
        Paragraph("Full Transcript", H2),
        HRFlowable(width="100%", thickness=1),
        Spacer(1, 3*mm),
    ]
    for para in transcript.split("\n"):
        if para.strip():
            story.append(Paragraph(para.strip(), BD))

    doc.build(story)


# ── MAIN ──────────────────────────────────────────────────────────────────────

def main():
    if not ASSEMBLYAI_API_KEY:
        print("ERROR: ASSEMBLYAI_API_KEY is not set.")
        print("Open a new terminal, run: set ASSEMBLYAI_API_KEY=your_key")
        return

    # Find queue files
    queue_files = list(Path(OUTPUT_DIR).rglob("_vimeo_queue.json"))
    if not queue_files:
        print(f"No _vimeo_queue.json found under {OUTPUT_DIR}/")
        print("Run classroom_archive.py first.")
        return

    if len(queue_files) == 1:
        queue_file = queue_files[0]
        print(f"Using queue: {queue_file}")
    else:
        for i, f in enumerate(queue_files):
            print(f"  [{i+1}] {f}")
        queue_file = queue_files[int(input("Select queue: ").strip()) - 1]

    queue = json.loads(queue_file.read_text(encoding="utf-8"))
    print(f"\n{len(queue)} video(s) to process\n")

    for entry in queue:
        title      = entry["title"]
        url        = entry["url"]
        password   = entry.get("password", "")
        lesson_dir  = Path(OUTPUT_DIR) / safe_name(entry["course"]) / safe_name(entry["lesson"])
        transcripts = lesson_dir / "transcripts"
        audio_path  = lesson_dir / f"{safe_name(title)}.mp3"

        # Reuse existing transcript folder if one already has a transcript.txt
        existing = next(
            (d for d in transcripts.iterdir() if (d / "transcript.txt").exists()),
            None
        ) if transcripts.exists() else None

        if existing:
            ts_dir = existing
        else:
            ts_dir = transcripts / f"{safe_name(title)}_{datetime.now():%Y%m%d_%H%M%S}"
            ts_dir.mkdir(parents=True, exist_ok=True)

        transcript_path = ts_dir / "transcript.txt"
        map_path        = ts_dir / "concept_map.png"
        pdf_path        = ts_dir / "summary.pdf"

        print(f"\n🎬 {title}")
        print(f"   {url}  (pwd: {password or 'none'})")

        # ── Transcribe ────────────────────────────────────────────────────────
        if transcript_path.exists():
            print("   ⏭️  Transcript exists — skipping")
            transcript = transcript_path.read_text(encoding="utf-8")
        else:
            if audio_path.exists():
                print("   ⏭️  Audio exists — skipping download")
            else:
                print("   ⬇️  Downloading audio...")
                try:
                    download_audio(url, password, audio_path)
                except RuntimeError as e:
                    print(f"   ❌ Download failed: {e}")
                    continue

            print("   🎙️  Transcribing with AssemblyAI (speaker labels)...")
            try:
                transcript = transcribe_audio(audio_path)
            except Exception as e:
                print(f"   ❌ Transcription failed: {e}")
                continue

            transcript_path.write_text(
                f"# {title}\n# Source: {url}\n\n{transcript}",
                encoding="utf-8"
            )
            print(f"   ✅ Transcript saved")

        # ── Summary PDF ───────────────────────────────────────────────────────
        if not ANTHROPIC_API_KEY:
            print("   ⚠️  ANTHROPIC_API_KEY not set — skipping summary PDF")
            continue

        if pdf_path.exists():
            print("   ⏭️  PDF exists — skipping")
            continue

        print("   🤖 Generating summary...")
        try:
            data = get_summary(transcript, title)
        except Exception as e:
            print(f"   ❌ Summary failed: {e}")
            continue

        print("   🗺️  Building concept map...")
        try:
            build_concept_map(data["concepts"], data["edges"], title, map_path)
        except Exception as e:
            print(f"   ⚠️  Concept map failed: {e}")

        print("   📄 Building PDF...")
        try:
            build_pdf(data, transcript, title, map_path, pdf_path)
            print(f"   💾 PDF saved → {pdf_path.name}")
        except Exception as e:
            print(f"   ❌ PDF failed: {e}")

    print("\n🎉 All done!")


if __name__ == "__main__":
    main()
