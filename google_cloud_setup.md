# Google Cloud Setup Guide

This is a one-time setup. Once done you never need to touch Google Cloud again
unless the credentials expire (they don't for desktop apps).

Estimated time: 10–15 minutes.

---

## Step 1 — Create a Google Cloud project

1. Go to https://console.cloud.google.com
2. Sign in with the **same Google account** you use for Google Classroom
3. Click the project dropdown at the top (it may say "Select a project")
4. Click **New Project**
5. Name it `Classroom Archiver` and click **Create**
6. Make sure the new project is selected in the dropdown before continuing

---

## Step 2 — Enable the three APIs

You need to enable three APIs. Do them one at a time:

**Classroom API:**
1. Go to https://console.cloud.google.com/apis/library
2. Search for `Google Classroom API`
3. Click it, then click **Enable**

**Drive API:**
1. Search for `Google Drive API`
2. Click it, then click **Enable**

**Slides API:**
1. Search for `Google Slides API`
2. Click it, then click **Enable**

---

## Step 3 — Configure the OAuth consent screen

Before creating credentials you need to tell Google what your app is.

1. Go to https://console.cloud.google.com/apis/credentials/consent
2. Select **External** and click **Create**
3. Fill in the required fields:
   - App name: `Classroom Archiver`
   - User support email: your email address
   - Developer contact email: your email address
4. Click **Save and Continue** on each screen (you can leave everything else blank)
5. On the **Scopes** screen, click **Save and Continue** (no changes needed)
6. On the **Test Users** screen, click **+ Add Users**
   - Add your Google account email address
   - Click **Add**, then **Save and Continue**
7. Click **Back to Dashboard**

---

## Step 4 — Create OAuth credentials

1. Go to https://console.cloud.google.com/apis/credentials
2. Click **+ Create Credentials** at the top
3. Choose **OAuth 2.0 Client ID**
4. Application type: **Desktop app**
5. Name: `Classroom Archiver Desktop`
6. Click **Create**
7. A dialog will appear — click **Download JSON**
8. Rename the downloaded file to exactly: `credentials.json`
9. Move it into the `classroom-archiver` project folder

---

## Step 5 — First run (browser authorisation)

Open a terminal in the `classroom-archiver` folder and run:

```
python classroom_archive.py
```

A browser tab will open asking you to sign in to Google.
Sign in with the same account enrolled in the course.

You will see a warning: **"Google hasn't verified this app"**
This is expected because this is a personal script, not a published app.
Click **Advanced** → **Go to Classroom Archiver (unsafe)** → **Continue**

Grant all the permissions it requests (Classroom, Drive, Slides access).

Once done, the browser tab will say "The authentication flow has completed."
Close it and return to the terminal — the script will continue automatically.

A file called `token.json` will appear in the folder. This is your saved login.
You will not need to do the browser step again unless you delete this file.

---

## Troubleshooting

**"Access blocked: Classroom Archiver has not completed the Google verification process"**
→ You may have skipped the Test Users step. Go back to the OAuth consent screen,
  add your email as a test user (Step 3, bullet 6), and try again.

**"redirect_uri_mismatch" error**
→ You may have created a Web Application credential instead of a Desktop app.
  Delete it and repeat Step 4, making sure to select "Desktop app".

**The browser window opens but nothing happens after I approve**
→ Wait 10–15 seconds. If still stuck, close the browser, delete any partial
  `token.json` file, and re-run the script.

**"This app isn't verified" / scam warning**
→ This is normal for personal scripts. Click Advanced → Go to Classroom Archiver → Continue.
