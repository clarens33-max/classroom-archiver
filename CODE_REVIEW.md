# Code Review Report — classroom-archiver

**Date:** 2026-04-12  
**Files reviewed:** `classroom_archive.py`, `classroom_transcribe.py`

---

## 1. Correctness

### classroom_archive.py
- **Slide dimensions (line ~130)**: Magic-number fallback for missing `magnitude` field is undocumented; if the API changes structure, defaults will silently produce wrong slide sizes.
- **Drive file double-nesting (line ~296-298)**: Only one level of `driveFile` nesting is unwrapped. Deeper nesting silently fails to extract the file ID.
- **Link extraction (line ~185-201)**: Only certain slide element types (textElements, tableRows, elementGroup) are scanned for links. Images with alt-text and video embeds are missed.

### classroom_transcribe.py
- **Claude JSON parsing (line ~125)**: Strips markdown fences via regex but doesn't validate the result before calling `json.loads()`. A truncated or malformed Claude response will raise an uncaught exception.
- **Concept map edge validation (line ~134-135)**: Doesn't guard against an empty `concepts` list before building the graph, leaving an invalid state.
- **Directory iteration (line ~245)**: `transcripts.iterdir()` is called without first checking that the directory exists — will raise `FileNotFoundError` in some failure scenarios.

---

## 2. Security

| Severity | File | Line(s) | Issue | Fix |
|----------|------|---------|-------|-----|
| **HIGH** | classroom_archive.py | ~70-71, 281-289 | Passwords extracted from descriptions and written to plain-text `_vimeo_queue.json` + printed to stdout | Stop logging passwords to console. If persistence is needed, restrict file permissions. |
| **HIGH** | classroom_transcribe.py | ~69 | Password passed to yt-dlp as a command-line argument (visible in process list, audit logs) | Pass via stdin, temp file, or env var instead of CLI arg. |
| **MEDIUM** | classroom_archive.py | ~137 | Bearer token held in memory and included in HTTP headers; any exception handler that dumps request context would leak it | Redact tokens from error output; use logging filters. |
| **MEDIUM** | classroom_transcribe.py | ~53-54 | API keys default to empty string — code continues, then fails with a cryptic error later | Check at startup: `if not ANTHROPIC_API_KEY: raise ValueError("ANTHROPIC_API_KEY not set")` |

---

## 3. Robustness

| Severity | File | Line(s) | Issue | Fix |
|----------|------|---------|-------|-----|
| **MEDIUM** | classroom_archive.py | ~144, 154 | `requests.get()` calls have no timeout — a slow server hangs the whole run | Add `timeout=30` |
| **MEDIUM** | classroom_archive.py | ~84-89 | No error handling if `credentials.json` is missing or OAuth fails | Wrap in try-except with a clear message pointing to `google_cloud_setup.md` |
| **MEDIUM** | classroom_transcribe.py | ~245 | `iterdir()` on a directory that may not exist | Add `if transcripts.exists():` guard |
| **MEDIUM** | classroom_transcribe.py | ~314 | Assumes `data["summary"]` and `data["takeaways"]` exist — will crash with `KeyError` on malformed Claude response | Add `.get()` with fallbacks |
| **MEDIUM** | classroom_archive.py | all | No retry/backoff for transient API errors (429, 503) | Wrap API calls in a simple exponential-backoff retry loop |
| **LOW** | classroom_archive.py | ~223, 242 | Corrupted/partial files are never re-downloaded (existence check is binary) | Add an option to force-refresh or check file size > 0 |
| **LOW** | classroom_archive.py | ~110 | Only the first regex match for passwords is used — multiple passwords or false positives silently use the wrong one | Log all matches; warn if more than one found |

---

## 4. Code Quality

- **Duplicated `safe_name()` function**: Defined independently in both files. Should live in a shared `utils.py`.
- **Magic numbers**: EMU constants (`914400`, `9144000`, `5143500`) are unexplained. Define as named constants with a comment.
- **Inconsistent error handling**: Some exceptions are printed and swallowed, others crash the script, others silently return `False`. No coherent logging strategy.
- **Bare `except Exception`** in classroom_transcribe.py (~lines 280, 303, 310, 317): hides programming errors; catch specific exceptions.
- **No docstrings** on major functions (`export_slides`, `download_drive_file`, `process_attachments`, etc.).
- **No type hints** in either file.
- **Long Claude prompt hard-coded inline** (classroom_transcribe.py ~line 100-113): should be a module-level constant.
- **Inconsistent path types**: some functions take `str`, others take `Path` objects.

---

## 5. Performance

| File | Line(s) | Issue | Fix |
|------|---------|-------|-----|
| classroom_archive.py | ~168 | Unexplained `time.sleep(0.25)` in slide export loop (100 slides = 25 s idle) | Document the reason (rate limiting?) or remove |
| classroom_archive.py | all | All API calls are sequential — no parallelisation or batch requests | Use `concurrent.futures.ThreadPoolExecutor` for independent materials |
| classroom_transcribe.py | ~39-40 | `matplotlib.use('Agg')` called inside a function on every invocation | Move to module-level initialisation |
| classroom_transcribe.py | ~199-201 | Full transcript rendered into PDF — hours-long recordings produce enormous PDFs | Add a page/character limit or split into appendix |

---

## 6. Actionable Issue List (Priority Order)

### High — Fix before any production run
1. **Password CLI exposure** (classroom_transcribe.py ~69): Pass Vimeo password via stdin or env var, not CLI arg.
2. **Password in JSON + stdout** (classroom_archive.py ~281-289): Stop printing passwords to console; consider restricting `_vimeo_queue.json` permissions.

### Medium — Fix soon
3. Add `timeout=30` to all `requests.get()` calls (classroom_archive.py ~144, 154).
4. Validate API keys at startup (classroom_transcribe.py ~53-54).
5. Wrap `credentials.json` / OAuth in try-except with a helpful message (classroom_archive.py ~84-89).
6. Guard `transcripts.iterdir()` with an existence check (classroom_transcribe.py ~245).
7. Wrap Claude JSON parsing in try-except with a descriptive error (classroom_transcribe.py ~125).
8. Add `.get()` with fallbacks for `data["summary"]` / `data["takeaways"]` (classroom_transcribe.py ~314).

### Low — Clean up when convenient
9. Move `safe_name()` to a shared module.
10. Replace magic EMU numbers with named constants.
11. Document or remove the 0.25 s sleep in the slide export loop.
12. Consolidate repetitive file-skip checks in classroom_transcribe.py into a helper function.
13. Add docstrings and type hints to public functions.
14. Move the Claude prompt to a module-level constant.

---

## Summary

The two scripts are functional and well-structured for a personal archival tool. The main concerns are:

- **Two high-severity security issues** around credential/password exposure that should be addressed before running on sensitive content.
- **Several medium robustness gaps** (no timeouts, no retry logic, missing existence checks) that could cause silent failures or indefinite hangs.
- **Code quality debt** that is low-risk but would make the codebase easier to maintain.
