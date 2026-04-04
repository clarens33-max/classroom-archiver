"""
upload_to_gdrive.py — Upload course archive files to Google Drive.

Uploads every non-audio, non-text file from output/ into a mirrored
folder tree under a "classroom-archiver" root folder in My Drive.
Makes every file publicly accessible (anyone with link can view).
Saves the path→file-ID mapping to gdrive_map.json.

Safe to re-run: already-uploaded files are detected and skipped.

Usage:
    python upload_to_gdrive.py
"""

import json
import mimetypes
import os
import sys
from pathlib import Path

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

# ── Config ────────────────────────────────────────────────────────────────────

SCOPES = ["https://www.googleapis.com/auth/drive"]
CREDENTIALS_FILE = "credentials.json"
TOKEN_FILE = "gdrive_token.json"          # separate from classroom_archive token
OUTPUT_DIR = Path("output")
MAP_FILE = Path("gdrive_map.json")
DRIVE_ROOT_FOLDER_NAME = "classroom-archiver"

# File extensions to upload (non-audio, non-plain-text binaries)
UPLOAD_EXTS = {".pdf", ".png", ".pptx", ".ppt", ".xlsx", ".xls", ".docx", ".doc"}

# Audio/video to always skip
SKIP_EXTS = {".mp3", ".mp4", ".m4a", ".wav", ".aac", ".ogg", ".webm",
             ".part", ".ytdl"}


# ── Auth ──────────────────────────────────────────────────────────────────────

def get_service():
    creds = None
    if Path(TOKEN_FILE).exists():
        creds = Credentials.from_authorized_user_file(TOKEN_FILE, SCOPES)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            if not Path(CREDENTIALS_FILE).exists():
                print(f"ERROR: {CREDENTIALS_FILE} not found.")
                sys.exit(1)
            flow = InstalledAppFlow.from_client_secrets_file(CREDENTIALS_FILE, SCOPES)
            creds = flow.run_local_server(port=0)
        with open(TOKEN_FILE, "w") as f:
            f.write(creds.to_json())
    return build("drive", "v3", credentials=creds)


# ── Drive helpers ─────────────────────────────────────────────────────────────

def find_or_create_folder(service, name, parent_id):
    """Return the Drive folder ID for name under parent, creating if needed."""
    safe_name = name.replace("'", "\\'")
    q = (
        f"name='{safe_name}' and "
        f"mimeType='application/vnd.google-apps.folder' and "
        f"'{parent_id}' in parents and trashed=false"
    )
    res = service.files().list(q=q, fields="files(id)", pageSize=1).execute()
    hits = res.get("files", [])
    if hits:
        return hits[0]["id"]
    meta = {
        "name": name,
        "mimeType": "application/vnd.google-apps.folder",
        "parents": [parent_id],
    }
    folder = service.files().create(body=meta, fields="id").execute()
    return folder["id"]


def find_existing_file(service, name, parent_id):
    """Return file ID if a non-trashed file with this name exists under parent."""
    safe_name = name.replace("'", "\\'")
    q = f"name='{safe_name}' and '{parent_id}' in parents and trashed=false"
    res = service.files().list(q=q, fields="files(id)", pageSize=1).execute()
    hits = res.get("files", [])
    return hits[0]["id"] if hits else None


def upload_file(service, local_path, parent_id, filename):
    """Upload a file to Drive and return its ID. Skips if already uploaded."""
    existing = find_existing_file(service, filename, parent_id)
    if existing:
        return existing, True  # (id, was_skipped)

    mime, _ = mimetypes.guess_type(str(local_path))
    if not mime:
        mime = "application/octet-stream"
    media = MediaFileUpload(str(local_path), mimetype=mime, resumable=True)
    meta = {"name": filename, "parents": [parent_id]}
    f = service.files().create(body=meta, media_body=media, fields="id").execute()
    return f["id"], False


def make_public(service, file_id):
    """Grant anyone-with-link read access."""
    service.permissions().create(
        fileId=file_id,
        body={"type": "anyone", "role": "reader"},
    ).execute()


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    if not OUTPUT_DIR.exists():
        print("output/ directory not found — run classroom_archive.py first.")
        sys.exit(1)

    # Load existing map so we can resume
    gdrive_map = {}
    if MAP_FILE.exists():
        gdrive_map = json.loads(MAP_FILE.read_text(encoding="utf-8"))

    print("Authenticating with Google Drive…")
    service = get_service()

    # Find or create root folder
    root_id = find_or_create_folder(service, DRIVE_ROOT_FOLDER_NAME, "root")
    print(f"Drive root folder: {DRIVE_ROOT_FOLDER_NAME} (id={root_id})")

    # Walk output/, collect files to upload
    files_to_upload = []
    for local_path in sorted(OUTPUT_DIR.rglob("*")):
        if not local_path.is_file():
            continue
        ext = local_path.suffix.lower()
        if ext in SKIP_EXTS:
            continue
        if ext not in UPLOAD_EXTS:
            continue  # skip .txt and other text files — they stay in git
        files_to_upload.append(local_path)

    total = len(files_to_upload)
    print(f"\nFiles to upload: {total}")
    if total == 0:
        print("Nothing to upload.")
        return

    # Cache of local-relative-path → Drive folder ID
    folder_id_cache = {}

    uploaded = 0
    skipped = 0
    errors = 0

    for i, local_path in enumerate(files_to_upload, 1):
        rel = str(local_path.relative_to(OUTPUT_DIR)).replace("\\", "/")

        # Check map — skip entirely if already mapped
        if rel in gdrive_map:
            skipped += 1
            print(f"  [{i}/{total}] SKIP (mapped)  {rel}")
            continue

        # Build folder path on Drive
        rel_parts = Path(rel).parts  # e.g. ('CourseName', 'M01_...', 'resources', 'file.pdf')
        parent_id = root_id
        for part in rel_parts[:-1]:  # all but filename
            cache_key = "/".join(rel_parts[: rel_parts.index(part) + 1])
            if cache_key not in folder_id_cache:
                folder_id_cache[cache_key] = find_or_create_folder(service, part, parent_id)
            parent_id = folder_id_cache[cache_key]

        filename = rel_parts[-1]
        try:
            file_id, was_skipped = upload_file(service, local_path, parent_id, filename)
            if not was_skipped:
                make_public(service, file_id)
            gdrive_map[rel] = file_id
            # Save map after every file so progress isn't lost on interruption
            MAP_FILE.write_text(json.dumps(gdrive_map, indent=2), encoding="utf-8")
            if was_skipped:
                skipped += 1
                print(f"  [{i}/{total}] SKIP (exists) {rel}")
            else:
                uploaded += 1
                size_kb = local_path.stat().st_size // 1024
                print(f"  [{i}/{total}] UPLOAD {size_kb}KB  {rel}")
        except Exception as e:
            errors += 1
            print(f"  [{i}/{total}] ERROR: {e}  ({rel})")

    print(f"\nDone. Uploaded: {uploaded}  Skipped: {skipped}  Errors: {errors}")
    print(f"Map saved to {MAP_FILE}  ({len(gdrive_map)} entries total)")
    if errors:
        print("Re-run the script to retry failed files.")


if __name__ == "__main__":
    main()
