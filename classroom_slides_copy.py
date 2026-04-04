"""
classroom_slides_copy.py
────────────────────────────────────────────────────────────────────────────────
For each slide deck PDF that couldn't be downloaded directly:
  1. Uses Drive API to "Make a copy" into your own Drive
  2. Downloads the copy as PDF
  3. Deletes the copy from your Drive (cleanup)

Prerequisites: same credentials.json / token.json as classroom_archive.py
────────────────────────────────────────────────────────────────────────────────
"""

import io
import os
import re
from pathlib import Path

from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload

os.environ["OAUTHLIB_RELAX_TOKEN_SCOPE"] = "1"

SCOPES = [
    "https://www.googleapis.com/auth/classroom.courses.readonly",
    "https://www.googleapis.com/auth/classroom.coursework.me.readonly",
    "https://www.googleapis.com/auth/classroom.courseworkmaterials.readonly",
    "https://www.googleapis.com/auth/classroom.announcements.readonly",
    "https://www.googleapis.com/auth/drive",   # full drive needed to copy + delete
]

COURSE_DIR = Path("output/AI_Solutions_Architecture_with_Toby_Fotherby")

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


def safe_name(s, max_len=80):
    s = re.sub(r'[\\/*?:"<>|]', "_", str(s))
    return re.sub(r"\s+", "_", s.strip())[:max_len]


def get_drive():
    creds = None
    if os.path.exists("token.json"):
        creds = Credentials.from_authorized_user_file("token.json", SCOPES)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file("credentials.json", SCOPES)
            creds = flow.run_local_server(port=0)
        with open("token.json", "w") as f:
            f.write(creds.to_json())
    return build("drive", "v3", credentials=creds)


def main():
    print("🔐 Authenticating...")
    drive = get_drive()
    print("✅ Authenticated\n")

    for deck in SLIDES:
        name    = deck["name"]
        file_id = deck["file_id"]
        out_dir = COURSE_DIR / deck["folder"]
        out_dir.mkdir(parents=True, exist_ok=True)
        dest    = out_dir / f"{safe_name(name)}.pdf"

        if dest.exists() and dest.stat().st_size > 100_000:
            print(f"⏭️  Exists: {name}")
            continue

        print(f"📄 {name}")

        # Step 1: Make a copy in your Drive
        print(f"   📋 Copying to your Drive...")
        try:
            copy = drive.files().copy(
                fileId=file_id,
                body={"name": f"_tmp_{safe_name(name)}"},
            ).execute()
            copy_id = copy["id"]
        except Exception as e:
            print(f"   ❌ Copy failed: {e}")
            continue

        # Step 2: Download the copy
        print(f"   ⬇️  Downloading...")
        try:
            req = drive.files().get_media(fileId=copy_id)
            buf = io.BytesIO()
            dl  = MediaIoBaseDownload(buf, req)
            done = False
            while not done:
                _, done = dl.next_chunk()
            dest.write_bytes(buf.getvalue())
            print(f"   💾 Saved → {dest.name} ({dest.stat().st_size // 1024}KB)")
        except Exception as e:
            print(f"   ❌ Download failed: {e}")
        finally:
            # Step 3: Delete the copy from your Drive
            try:
                drive.files().delete(fileId=copy_id).execute()
                print(f"   🗑️  Copy deleted from Drive")
            except Exception:
                print(f"   ⚠️  Could not delete copy (id: {copy_id}) — delete manually from Drive")

    print("\n🎉 Done!")


if __name__ == "__main__":
    main()
