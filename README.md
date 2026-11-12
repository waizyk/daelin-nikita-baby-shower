# 🍯 Daelin & Nikita — Baby Shower Photo Site

A complete little website for the baby shower on **Saturday, 5 December 2026**:

| Page | What it does |
|---|---|
| `/` | The invitation — date, time, venue, RSVP, dress code, gifts, plus a QR code |
| `/photos` | The photo album (only host-approved photos appear here) |
| `/upload` | Where the QR code sends guests — upload photos straight from a phone |
| `/host` | Host desk — approve / decline / delete photos (PIN protected) |
| `/host/zip` | Downloads every approved photo in one zip |
| `/sign` | A printable A5/A4 "scan me" sign for the party table (with the real QR) |

Everything is themed pastel Winnie the Pooh with hand-drawn SVG illustrations,
no external images or fonts, so it works offline and loads instantly.

---

## 1. Fill in the party details (2 minutes)

Open **`config.py`** and replace everything in `[square brackets]`:

```python
EVENT = {
    "parents": "Daelin & Nikita",
    "time": "[Time — e.g. 11:00 for 11:30]",   ← change me
    "venue": "[Venue name]",                   ← change me
    "address": "[Street address, suburb, city]",
    "rsvp": "[RSVP name & number]",
    "hosts": "[Host name(s)]",
    ...
}

HOST_PIN = "hundredacre"                        ← change this to your own PIN
```

Anything still in `[brackets]` shows up **highlighted in pink** on the page, so
you can't miss a placeholder before you share the link. When a field is filled
in, the highlight disappears.

Other settings in `config.py`:

- `auto_approve` — leave `False` so hosts approve every photo.
- `max_photos_per_guest` — how many photos one guest can add at a time (12).
- `registry_url` / `map_url` — optional links.
- `drive_url` — already set to the Google Drive folder that was created for you.

Restart the site after editing (`Ctrl+C`, then start it again).

---

## 2. Run it locally

```bash
cd baby-shower-album
pip install -r requirements.txt
python app.py
```

Then open <http://localhost:8000>.

- Album: <http://localhost:8000/photos>
- Guest upload page: <http://localhost:8000/upload>
- Host desk (PIN `hundredacre` by default): <http://localhost:8000/host>
- Printable QR sign: <http://localhost:8000/sign>

Uploaded photos and the database live in `data/` — back that folder up (or copy
it to Drive) and nothing is ever lost.

---

## 3. Put it online so the QR code works for guests

The QR code always points at whichever address the site is running on, so **host
it first, then print the sign**. Any of these work — pick one:

### Option A — PythonAnywhere (free, easiest, no card)
1. Sign up at <https://www.pythonanywhere.com> → *Web* → *Add a new web app* → **Flask** → Python 3.10+.
2. Upload the `baby-shower-album` folder (Files tab → Upload a zip, then unzip in the Bash console).
3. `pip3 install --user -r requirements.txt` in the Bash console.
4. Edit the generated WSGI file so it points at the app:
   ```python
   import sys
   sys.path.insert(0, "/home/YOURUSER/baby-shower-album")
   from app import app as application
   ```
5. Click **Reload**. Your site is at `YOURUSER.pythonanywhere.com`.
6. The free tier needs one click every 3 months to stay awake — plenty.
7. Note: free PythonAnywhere can *download* from Drive but guests uploading is
   all handled by the app itself, so nothing else is needed.

### Option B — Render (free tier, auto-deploys from GitHub)
1. Push this folder to a GitHub repo.
2. On <https://render.com> → *New* → *Web Service* → connect the repo.
3. Build command: `pip install -r requirements.txt`
4. Start command: `gunicorn app:app --bind 0.0.0.0:$PORT`
   (add `gunicorn` to `requirements.txt`, or use `python app.py`).
5. **Important:** free Render instances wipe their disk on redeploy/restart —
   add a paid disk, or download the zip from `/host/zip` after the shower.

### Option C — your own VPS / Raspberry Pi on the venue wifi
```bash
pip install -r requirements.txt gunicorn
gunicorn app:app -b 0.0.0.0:8000 --workers 2
```
Guests scan the QR, which resolves to `http://<your-lan-ip>:8000/upload`. Ideal
when the venue wifi is unreliable — no internet needed at all.

> **Do this before printing:** open `/sign` **on the finished domain** and hit
> *Print this sign*. The QR is generated from the live URL, so it can never be
> stale.

---

## 4. On the day

- Print `/sign` (A4 portrait, one page) and put it on the gift table, the food
  table, and by the door. Two copies is plenty for ~40 guests.
- Text the album link in the family group as well — some guests prefer tapping.
- **Host desk → Waiting**: tap *Approve* and the photo appears in the album
  instantly. *Decline* hides it (you can still see it under *Declined*).
- At the end of the day: *Download approved (.zip)* → drop it into the Google
  Drive folder → you have a permanent copy plus a printable album.
- Guests with flaky data can use the *Google Drive folder* link at the bottom of
  the upload page — everything in Drive gets added to the album by the hosts.

---

## 5. How approvals work (and where to change it)

```
Guest uploads ─▶ status = "pending"  (invisible to everyone but hosts)
Host approves ─▶ status = "approved" (shows in /photos)
Host declines ─▶ status = "rejected" (hidden, recoverable)
```

- Pending photos are **not** served publicly: `/media/full/<id>.jpg` returns 404
  unless you're signed in as a host.
- The host session is a signed cookie; `HOST_PIN` and `SECRET_KEY` are in
  `config.py` — change both before sharing the link.
- Want photos visible immediately (no approval)? Set `"auto_approve": True`.

---

## 6. Files

```
baby-shower-album/
├── app.py               Flask app: pages, uploads, approvals, QR, zip export
├── config.py            ← everything you need to edit lives here
├── requirements.txt
├── templates/
│   ├── base.html        header, footer, nav
│   ├── index.html       the invitation page
│   ├── album.html       the photo album + lightbox
│   ├── upload.html      guest upload page
│   ├── host.html        host approval desk
│   ├── host_login.html  PIN screen
│   ├── sign.html        printable "scan me" sign (self-contained, print-ready)
│   └── _art.html        hand-drawn Pooh / Piglet / Eeyore / bees / hunny pot SVG
├── static/style.css     the whole pastel theme
└── data/                created at runtime — database + photos (back this up)
```

Photos are resized on upload: a 1800px "full" version and a ~760px thumbnail,
both JPEG — so a 6 MB phone photo becomes ~350 KB and the album stays fast even
on 3G.

iPhone HEIC files are supported out of the box (via `pillow-heif`). If a guest's
file can't be decoded it's skipped and they're told, rather than silently lost.

---

## 7. Nice-to-haves you can add later

- **Add a password to the whole album** (for privacy): wrap `/photos` in a
  session check, same pattern as `/host`.
- **Auto-post approved photos to Drive:** after `host_action` approving, call the
  Drive API — or simply use the zip export, which is one click.
- **Slideshow for the party TV:** `/photos` in full-screen Chrome with a bit of
  auto-scroll works well; or duplicate `album.html` and add a rotating carousel.

Made with honey and love. Enjoy the shower 🐻🍯
