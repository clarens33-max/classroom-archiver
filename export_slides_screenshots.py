"""
export_slides_screenshots.py
────────────────────────────────────────────────────────────────────────────────
Exports each lesson's slide deck as individual PNG screenshots.

Uses the existing token.json / credentials.json OAuth session to export each
Google Slides file via the Drive API, then renders every page to a PNG using
PyMuPDF.

Output structure:
  slides_screenshots/
    Lesson_00_Welcome_Lesson/
      page_001.png
      page_002.png
      ...
    Lesson_01_AI_Solutions_Architecture/
      page_001.png
      ...
────────────────────────────────────────────────────────────────────────────────
"""

import os
import io
import re
import sys
from pathlib import Path

import fitz  # PyMuPDF
import requests

from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from google_auth_oauthlib.flow import InstalledAppFlow

# ── CONFIG ────────────────────────────────────────────────────────────────────

BASE_DIR    = Path(__file__).parent
OUT_DIR     = BASE_DIR / "slides_screenshots"
CREDS_FILE  = BASE_DIR / "credentials.json"
TOKEN_FILE  = BASE_DIR / "token.json"
DPI         = 150   # resolution for PNG renders (150 = ~1920×1080 for a 16:9 slide)

SCOPES = [
    "https://www.googleapis.com/auth/classroom.courses.readonly",
    "https://www.googleapis.com/auth/classroom.courseworkmaterials.readonly",
    "https://www.googleapis.com/auth/drive.readonly",
]

# Drive file IDs taken from classroom_slides_screenshot.py
SLIDES = [
    {"folder": "Lesson_00_Welcome_Lesson",                    "file_id": "1NLVkzNiGTXVbVceNKC3kHnXjRRnAUgJR", "name": "0. Welcome Lesson"},
    {"folder": "Lesson_01_AI_Solutions_Architecture",         "file_id": "1Y_p6wbRzbnmHppiQKRfq32FC6wLq9U_b", "name": "Lesson 1. AI Solutions Architecture"},
    {"folder": "Lesson_02_Core_AI_ML_Algorithms",             "file_id": "10mGQP-GaFYctvpP4eB506g_QuK887DBt", "name": "Lesson 2. Core AI-ML Algorithms and Concepts"},
    {"folder": "Lesson_03_Model_Training_Fundamentals",       "file_id": "1QIRsefTfDNGf0kF_6lrSQB4TA0hHnPLJ", "name": "Lesson 3. Model Training Fundamentals"},
    {"folder": "Lesson_04_Advanced_AI_ML_Algorithms",         "file_id": "1NoPB4neTEUCFs9yEt1k1oa3Z9pTMRpU9", "name": "Lesson 4. Advanced AI-ML Algorithms and Concepts"},
    {"folder": "Lesson_05_NLP_Document_Image_Processing",     "file_id": "1zn7l4hd3-Bo3WbFtTZ0Rutz7znJfzk49", "name": "Lesson 5. NLP Document and Image Processing"},
    {"folder": "Lesson_06_Embeddings_Vector_Databases",       "file_id": "1N3icrEtkmx2dsFJbzoGDHsTZSfR-hcpI", "name": "Lesson 6. Embeddings and Vector Databases"},
    {"folder": "Lesson_07_Generative_AI_Solutions",           "file_id": "1JiqmXF5Qdz7tLbB82RpIuHtrHUL9NS-L", "name": "Lesson 7. Generative AI Solutions"},
    {"folder": "Lesson_08_GenAI_Application_Architecture",    "file_id": "1_uxh-nqfGMWeh6m05KRz8RJiUDbRbTT8", "name": "Lesson 8. GenAI Application Architecture Patterns"},
    {"folder": "Lesson_09_Designing_Solutions",               "file_id": "1hs0CRTnm5y9h0UJyIECTECvuYiSvjCrx", "name": "Lesson 9. Designing Solutions Principles and Good Practices"},
    {"folder": "Lesson_10_Cloud_Based_Services",              "file_id": "1ce3wsWFe-IgRFydyoctLoxr38dbz6HKM", "name": "Lesson 10. Leveraging Cloud Based Services"},
    {"folder": "Lesson_11_RAG_Deep_Dive",                     "file_id": "1LBmE1aEjMfx-NbL3cJ5H8a2JwoO0z2tB", "name": "Lesson 11. Retrieval Augmented Generation"},
    {"folder": "Lesson_12_GenAI_Agents",                      "file_id": "1JRpBYJFTu5E9rnp_lblfAWtyslQfMAZt", "name": "Lesson 12. GenAI Agents"},
    {"folder": "Lesson_13_MLOps",                             "file_id": "1sPaQ0rTLVqAxH6yAXKodo-TxOBkbjPbr", "name": "Lesson 13. Machine Learning Operations"},
    {"folder": "Lesson_14_Reliability_Performance_Cost",      "file_id": "1wJH6q7bq0mFStdVc1JzYJVL8vVI3LJHl", "name": "Lesson 14. Reliability Performance Efficiency and Cost Optimisation"},
    {"folder": "Lesson_15_Ethics_Bias_Model_Evaluation",      "file_id": "1nbI6R41pXhSa9g9CyZvZuy88YKouLxo7", "name": "Lesson 15. Ethics Bias and Model Evaluation"},
    {"folder": "Lesson_16_AI_Trends_Career_Outlook",          "file_id": "1DGi3LX3XiN_Cr2Z0ZXtO4u7xB4qm3Aqh", "name": "Lesson 16. AI Trends and Career Outlook"},
]

# ── AUTH ──────────────────────────────────────────────────────────────────────

def get_credentials():
    creds = None
    if TOKEN_FILE.exists():
        creds = Credentials.from_authorized_user_file(str(TOKEN_FILE), SCOPES)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(str(CREDS_FILE), SCOPES)
            creds = flow.run_local_server(port=0)
        TOKEN_FILE.write_text(creds.to_json())
    return creds

# ── EXPORT & RENDER ───────────────────────────────────────────────────────────

def export_slides_as_pdf(file_id: str, access_token: str) -> bytes | None:
    """Export a Google Slides file as PDF bytes via the Drive API."""
    # Google Slides export URL
    export_url = f"https://www.googleapis.com/drive/v3/files/{file_id}/export"
    headers = {"Authorization": f"Bearer {access_token}"}
    params = {"mimeType": "application/pdf"}

    resp = requests.get(export_url, headers=headers, params=params, stream=True)
    if resp.status_code == 200:
        return resp.content
    else:
        print(f"    ⚠️  Export failed: HTTP {resp.status_code} — {resp.text[:200]}")
        return None


def render_pdf_to_pngs(pdf_bytes: bytes, out_dir: Path, dpi: int = DPI):
    """Render each page of a PDF to a numbered PNG in out_dir."""
    out_dir.mkdir(parents=True, exist_ok=True)
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    total = len(doc)
    print(f"    📄 {total} pages")
    mat = fitz.Matrix(dpi / 72, dpi / 72)  # 72 dpi is PDF default
    for i, page in enumerate(doc, start=1):
        png_path = out_dir / f"page_{i:03d}.png"
        if png_path.exists():
            print(f"    ⏭️  page_{i:03d}.png already exists, skipping")
            continue
        pix = page.get_pixmap(matrix=mat)
        pix.save(str(png_path))
        print(f"    💾 page_{i:03d}.png  ({pix.width}×{pix.height}px)")
    doc.close()
    return total

# ── MAIN ──────────────────────────────────────────────────────────────────────

def main():
    print("🔑 Authenticating…")
    creds = get_credentials()
    access_token = creds.token
    print("✅ Authenticated\n")

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    print(f"📁 Output: {OUT_DIR}\n")

    success = 0
    failed = []

    for deck in SLIDES:
        folder_name = deck["folder"]
        file_id     = deck["file_id"]
        name        = deck["name"]
        out_dir     = OUT_DIR / folder_name

        print(f"📊 {name}")

        # Skip if already fully done (has at least 1 PNG)
        if out_dir.exists() and any(out_dir.glob("page_*.png")):
            existing = list(out_dir.glob("page_*.png"))
            print(f"    ⏭️  Already captured ({len(existing)} pages), skipping")
            success += 1
            continue

        print(f"    ⬇️  Exporting PDF from Drive…")
        pdf_bytes = export_slides_as_pdf(file_id, access_token)
        if not pdf_bytes:
            failed.append(name)
            print(f"    ❌ Skipping\n")
            continue

        print(f"    🖼️  Rendering pages…")
        try:
            total = render_pdf_to_pngs(pdf_bytes, out_dir)
            print(f"    ✅ Done — {total} pages saved to {folder_name}/\n")
            success += 1
        except Exception as e:
            print(f"    ❌ Render error: {e}\n")
            failed.append(name)

    print(f"{'─'*60}")
    print(f"✅ {success}/{len(SLIDES)} decks captured")
    if failed:
        print(f"❌ Failed: {', '.join(failed)}")
    print(f"\n📁 Screenshots saved to: {OUT_DIR.resolve()}")


if __name__ == "__main__":
    main()
