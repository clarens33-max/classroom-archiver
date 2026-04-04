"""
classroom_slides_screenshot.py
────────────────────────────────────────────────────────────────────────────────
Screenshots Google Drive PDF slide decks that cannot be downloaded.

Opens each PDF in the Drive viewer using Playwright, navigates page-by-page,
screenshots each slide, and stitches them into a PDF.

On first run: a browser window opens — log into Google when prompted, then
press Enter in the terminal. The session is saved for future runs.

Prerequisites:
  pip install playwright pillow
  playwright install chromium
────────────────────────────────────────────────────────────────────────────────
"""

import io
import os
import re
import time
from pathlib import Path

from PIL import Image
from playwright.sync_api import sync_playwright

# ── CONFIG ────────────────────────────────────────────────────────────────────

COURSE_DIR  = Path("output/AI_Solutions_Architecture_with_Toby_Fotherby")
SESSION_DIR = Path(".playwright_session")   # persisted login lives here
VIEWPORT    = {"width": 1440, "height": 900}

# File IDs extracted from the 403 errors during classroom_archive.py
SLIDES = [
    {"folder": "M75_Welcome_Lesson._Slides",  "file_id": "1NLVkzNiGTXVbVceNKC3kHnXjRRnAUgJR", "name": "0. Welcome Lesson"},
    {"folder": "M71_Lesson_1._Slides",        "file_id": "1Y_p6wbRzbnmHppiQKRfq32FC6wLq9U_b", "name": "Lesson 1. AI Solutions Architecture"},
    {"folder": "M65_Lesson_2._Slides",        "file_id": "10mGQP-GaFYctvpP4eB506g_QuK887DBt", "name": "Lesson 2. Core AI-ML Algorithms and Concepts"},
    {"folder": "M61_Lesson_3._Slides",        "file_id": "1QIRsefTfDNGf0kF_6lrSQB4TA0hHnPLJ", "name": "Lesson 3. Model Training Fundamentals"},
    {"folder": "M57_Lesson_4._Slides",        "file_id": "1NoPB4neTEUCFs9yEt1k1oa3Z9pTMRpU9", "name": "Lesson 4. Advanced AI-ML Algorithms and Concepts"},
    {"folder": "M54_Lesson_5._Slides",        "file_id": "1zn7l4hd3-Bo3WbFtTZ0Rutz7znJfzk49", "name": "Lesson 5. NLP Document and Image Processing"},
    {"folder": "M48_Lesson_6._Slides",        "file_id": "1N3icrEtkmx2dsFJbzoGDHsTZSfR-hcpI", "name": "Lesson 6. Embeddings and Vector Databases"},
    {"folder": "M45_Lesson_7._Slides",        "file_id": "1JiqmXF5Qdz7tLbB82RpIuHtrHUL9NS-L", "name": "Lesson 7. Generative AI Solutions"},
    {"folder": "M41_Lesson_8._Slides",        "file_id": "1_uxh-nqfGMWeh6m05KRz8RJiUDbRbTT8", "name": "Lesson 8. Generative AI Application Architecture Patterns"},
    {"folder": "M37_Lesson_9._Slides",        "file_id": "1hs0CRTnm5y9h0UJyIECTECvuYiSvjCrx", "name": "Lesson 9. Designing Solutions Principles and Good Practices"},
    {"folder": "M31_Lesson_10._Slides",       "file_id": "1ce3wsWFe-IgRFydyoctLoxr38dbz6HKM", "name": "Lesson 10. Leveraging Cloud Based Services"},
    {"folder": "M30_Lesson_11._Slides",       "file_id": "1LBmE1aEjMfx-NbL3cJ5H8a2JwoO0z2tB", "name": "Lesson 11. Retrieval Augmented Generation"},
    {"folder": "M25_Lesson_12._Slides",       "file_id": "1JRpBYJFTu5E9rnp_lblfAWtyslQfMAZt", "name": "Lesson 12. GenAI Agents and Promptflows"},
    {"folder": "M21_Lesson_13._Slides",       "file_id": "1sPaQ0rTLVqAxH6yAXKodo-TxOBkbjPbr", "name": "Lesson 13. Machine Learning Operations"},
    {"folder": "M18_Lesson_14._Slides",       "file_id": "1wJH6q7bq0mFStdVc1JzYJVL8vVI3LJHl", "name": "Lesson 14. Reliability Performance Efficiency and Cost Optimisation"},
    {"folder": "M10_Lesson_15._Slides",       "file_id": "1nbI6R41pXhSa9g9CyZvZuy88YKouLxo7", "name": "Lesson 15. Ethics Bias and Model Evaluation"},
    {"folder": "M05_Lesson_16._Slides",       "file_id": "1DGi3LX3XiN_Cr2Z0ZXtO4u7xB4qm3Aqh", "name": "Lesson 16. Trends and Career Outlook"},
]

# ── HELPERS ───────────────────────────────────────────────────────────────────

def safe_name(s):
    s = re.sub(r'[\\/*?:"<>|]', "_", str(s))
    return re.sub(r"\s+", "_", s.strip())


def get_page_count(page):
    """Try several methods to extract the total page count from Drive viewer."""
    # Method 1: look for an element showing "/ N" or "of N"
    for pattern in [r"/\s*(\d+)", r"of\s+(\d+)"]:
        try:
            els = page.locator(f"text=/{pattern}/").all()
            for el in els:
                txt = el.inner_text(timeout=1000)
                m = re.search(r"(\d+)", txt.replace(",", ""))
                if m:
                    n = int(m.group(1))
                    if n > 1:
                        return n
        except Exception:
            pass

    # Method 2: search raw page source
    try:
        html = page.content()
        for pat in [r'"numPages":\s*(\d+)', r'pageCount["\s:]+(\d+)',
                    r'data-page-count="(\d+)"']:
            m = re.search(pat, html)
            if m:
                n = int(m.group(1))
                if n > 0:
                    return n
    except Exception:
        pass

    return None   # unknown — caller will detect end via duplicate screenshots


def screenshot_deck(pw_page, file_id, out_dir, name, pause_for_zoom=False):
    """Navigate Drive viewer page-by-page and stitch screenshots into a PDF."""
    pdf_out = out_dir / f"{safe_name(name)}.pdf"
    if pdf_out.exists():
        if pdf_out.stat().st_size < 2 * 1024 * 1024:
            print(f"  🗑️  Deleting undersized PDF ({pdf_out.stat().st_size // 1024}KB) — recapturing…")
            pdf_out.unlink()
        else:
            print(f"  ⏭️  Already captured: {pdf_out.name}")
            return

    out_dir.mkdir(parents=True, exist_ok=True)

    print(f"  🌐 Opening Drive viewer…")
    pw_page.goto(
        f"https://drive.google.com/file/d/{file_id}/view",
        wait_until="domcontentloaded", timeout=30000,
    )
    pw_page.wait_for_timeout(4000)

    # Redirect to login?
    if "accounts.google.com" in pw_page.url:
        print("  ⚠️  Not logged in — please log into Google in the browser,")
        print("      then press Enter here to continue…")
        input()
        pw_page.wait_for_timeout(3000)

    total = get_page_count(pw_page)
    if total:
        print(f"  📄 {total} pages detected")
    else:
        print(f"  📄 Page count unknown — will detect end automatically")
        total = 200

    if pause_for_zoom:
        # Try to set zoom to 125% automatically via the zoom input
        zoom_set = pw_page.evaluate("""() => {
            const inputs = document.querySelectorAll('input');
            for (const inp of inputs) {
                const v = inp.value.replace('%','').trim();
                const n = parseInt(v);
                if (!isNaN(n) && n >= 10 && n <= 500) {
                    inp.focus();
                    inp.value = 'Fit';
                    inp.dispatchEvent(new Event('input', {bubbles: true}));
                    inp.dispatchEvent(new KeyboardEvent('keydown', {key: 'Enter', keyCode: 13, bubbles: true}));
                    inp.dispatchEvent(new KeyboardEvent('keyup',  {key: 'Enter', keyCode: 13, bubbles: true}));
                    return true;
                }
            }
            return false;
        }""")
        pw_page.wait_for_timeout(1500)
        if not zoom_set:
            print("  ⏸️  Could not set zoom automatically. Please set to 125% and press Enter…")
            input()
            pw_page.wait_for_timeout(1000)

    # Navigate to page 2 then back to page 1 to reset the scroll position
    for reset_page in [2, 1]:
        pw_page.evaluate(f"""() => {{
            const inputs = document.querySelectorAll('input');
            for (const inp of inputs) {{
                const v = parseInt(inp.value);
                if (!isNaN(v) && v >= 1) {{
                    inp.focus();
                    inp.value = '{reset_page}';
                    inp.dispatchEvent(new Event('input', {{bubbles: true}}));
                    inp.dispatchEvent(new KeyboardEvent('keydown', {{key: 'Enter', keyCode: 13, bubbles: true}}));
                    inp.dispatchEvent(new KeyboardEvent('keyup', {{key: 'Enter', keyCode: 13, bubbles: true}}));
                    return true;
                }}
            }}
        }}""")
        pw_page.wait_for_timeout(1000)

    pw_page.wait_for_timeout(500)

    images = []

    for page_num in range(1, total + 1):
        # Navigate to page by setting the input field via JavaScript
        # This clears the field first, then sets the value, then fires Enter
        success = pw_page.evaluate(f"""() => {{
            const inputs = document.querySelectorAll('input');
            for (const inp of inputs) {{
                const v = parseInt(inp.value);
                if (!isNaN(v) && v >= 1) {{
                    inp.focus();
                    inp.value = '{page_num}';
                    inp.dispatchEvent(new Event('input', {{bubbles: true}}));
                    inp.dispatchEvent(new KeyboardEvent('keydown', {{key: 'Enter', keyCode: 13, bubbles: true}}));
                    inp.dispatchEvent(new KeyboardEvent('keyup', {{key: 'Enter', keyCode: 13, bubbles: true}}));
                    return true;
                }}
            }}
            return false;
        }}""")

        if not success:
            print(f"  ⚠️  Could not find page input on page {page_num}")
            break

        # Wait and verify the input now shows the correct page number
        for _ in range(10):
            pw_page.wait_for_timeout(500)
            current = pw_page.evaluate(f"""() => {{
                const inputs = document.querySelectorAll('input');
                for (const inp of inputs) {{
                    const v = parseInt(inp.value);
                    if (!isNaN(v) && v >= 1) return v;
                }}
                return -1;
            }}""")
            if current == page_num:
                break

        pw_page.wait_for_timeout(800)
        shot = pw_page.screenshot(full_page=False)
        images.append(Image.open(io.BytesIO(shot)).convert("RGB"))

        if page_num % 5 == 0:
            print(f"    … page {page_num}/{total}")

    if not images:
        print(f"  ❌ No pages captured for {name}")
        return

    print(f"  🔗 Stitching {len(images)} pages into PDF…")
    images[0].save(
        str(pdf_out),
        save_all=True,
        append_images=images[1:],
        resolution=150,
    )
    print(f"  💾 Saved → {pdf_out.name}")


# ── ASSIGNMENT GOOGLE DOCS ────────────────────────────────────────────────────
# These failed via the Drive/Slides API — captured via browser instead.

ASSIGNMENT_DOCS = [
    {
        "folder": "A08_Assignment_#2__Establishing_your_AI_ML_Development_Environme",
        "file_id": "1A0nHWsliDK2eev_fJwViju1a2RnU72femR0gFyraA14",
        "name": "Template Assignment 2 - Establishing your AI-ML Development Environment",
        "type": "doc",
    },
    {
        "folder": "A10_Assignment_#1__Self_Assessment_and_Identifying_Areas_for_Gro",
        "file_id": "1bTrFolZx-gixdTwcMofUrCpPFqa5PA7d-04p0JBlgJo",
        "name": "Template Assignment 1 - Self Assessment and Identifying Areas for Growth",
        "type": "doc",
    },
]


def save_doc_as_pdf(pw_page, file_id, out_dir, name):
    """Open a Google Doc in the browser and save it as PDF via Ctrl+P."""
    pdf_out = out_dir / f"{safe_name(name)}.pdf"
    if pdf_out.exists():
        print(f"  ⏭️  Already captured: {pdf_out.name}")
        return

    out_dir.mkdir(parents=True, exist_ok=True)

    # Use the Google Docs export URL directly — no print dialog needed
    export_url = f"https://docs.google.com/document/d/{file_id}/export?format=pdf"
    print(f"  ⬇️  Downloading via export URL…")

    response = pw_page.request.get(export_url)
    if response.status == 200:
        pdf_out.write_bytes(response.body())
        print(f"  💾 Saved → {pdf_out.name}")
    else:
        # Fallback: open in viewer and screenshot
        print(f"  ⚠️  Export failed (status {response.status}), falling back to screenshot…")
        doc_url = f"https://docs.google.com/document/d/{file_id}/view"
        pw_page.goto(doc_url, wait_until="domcontentloaded", timeout=30000)
        pw_page.wait_for_timeout(3000)
        screenshot_deck(pw_page, file_id, out_dir, name)


# ── MAIN ──────────────────────────────────────────────────────────────────────

def main():
    SESSION_DIR.mkdir(exist_ok=True)

    print("🚀 Starting browser…")

    with sync_playwright() as pw:
        ctx = pw.chromium.launch_persistent_context(
            user_data_dir=str(SESSION_DIR),
            channel="chrome",
            headless=False,
            viewport=VIEWPORT,
            args=[
                "--start-maximized",
                "--disable-blink-features=AutomationControlled",
            ],
            ignore_default_args=["--enable-automation"],
        )
        page = ctx.pages[0] if ctx.pages else ctx.new_page()

        # Already logged in via existing profile — just verify
        page.goto("https://drive.google.com", wait_until="domcontentloaded",
                  timeout=20000)
        page.wait_for_timeout(2000)

        if "accounts.google.com" in page.url:
            print("Unexpected: not logged in. Please log in, then press Enter…")
            input()
            page.wait_for_timeout(2000)

        print("✅ Logged in. Starting capture…\n")

        print("── Slide decks ──────────────────────────────")
        for deck in SLIDES:
            folder  = COURSE_DIR / deck["folder"]
            name    = deck["name"]
            file_id = deck["file_id"]
            print(f"\n📊 {name}")
            try:
                screenshot_deck(page, file_id, folder, name, pause_for_zoom=True)
            except Exception as e:
                print(f"  ❌ Failed: {e}")

        print("\n── Assignment Google Docs ────────────────────")
        for doc in ASSIGNMENT_DOCS:
            folder  = COURSE_DIR / doc["folder"]
            name    = doc["name"]
            file_id = doc["file_id"]
            print(f"\n📝 {name}")
            try:
                save_doc_as_pdf(page, file_id, folder, name)
            except Exception as e:
                print(f"  ❌ Failed: {e}")

        ctx.close()

    print("\n🎉 All done!")


if __name__ == "__main__":
    main()
