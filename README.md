# 🍯 Daelin & Nikita — Baby Shower Photo Site

A complete little website for the baby shower on **Saturday, 5 December 2026**:

| Page | What it does |
|---|---|
| `/` | The invitation — date, time, venue, map, dress code, hosts, gifts (RSVP optional), plus a QR code |
| `/photos` | The scrapbook: host-approved photos and videos as polaroids, with a slideshow |
| `/upload` | Where the QR code sends guests. Photos and videos go straight into the parents' Google Photos album |
| `/host` | Host desk: connect Google Photos, approve or hide uploads (PIN protected) |
| `/sign` | A printable A5/A4 "scan me" sign for the party table (with the real QR) |

Everything is themed like the classic storybook Winnie-the-Pooh: watercolour
illustrations, a hand-drawn "100 Aker Wood" map, "In Which..." chapter headings,
honey drips and hand-lettered notes. All images and fonts are self-hosted in
`static/` (nothing loads from the internet), so it still works on flaky venue wifi.

- Illustrations (`static/img/`) are original artwork made for this site in the
  classic pen-and-watercolour storybook style. They are not Disney artwork.
- Fonts: IM Fell English and Patrick Hand, both SIL Open Font License (licences in
  `static/fonts/`).

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
```

The host PIN and cookie secret are **not** in this file any more: they are
environment variables on the server (see section 3), so they never end up in
the public repo.

Anything still in `[brackets]` shows up **highlighted in pink** on the page, so
you can't miss a placeholder before you share the link. When a field is filled
in, the highlight disappears.

Other settings in `config.py`:

- `auto_approve` — leave `False` so hosts approve every photo.
- `max_photos_per_guest` — how many photos one guest can add at a time (12).
- `registry_url` / `map_url` — optional links.
- `drive_url` — already set to the Google Drive folder that was created for you.

Restart the site after editing (`Ctrl+C`, then start it again), then rebuild the
GitHub Pages preview: `python tools/build_preview.py` and commit `docs/`.

---

## 2. Run it locally

```bash
cd daelin-nikita-baby-shower
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

## 3. The live site: Cloudflare + Google Photos (free)

**Live address:** https://daelin-nikita.aceops.workers.dev (hosted on the ACE OPS Cloudflare account)

```
guest's phone ──8 MB chunks──▶ Cloudflare Worker ──▶ Google Photos album (parents' account, original quality)
                                     │
                                     └─ D1 database: who sent what, their message, approved or not
scrapbook (/photos) ◀── approved items only, images streamed from Google Photos
```

- Photos **and videos** (up to 2 GB each) upload straight into an album in the parents'
  Google Photos. That album is the permanent archive. It keeps everything, even items hidden on the site.
- Uploads resume by themselves if the signal drops mid-video.
- The scrapbook only shows what a host has approved. It refreshes itself every 30 s, and there's a slideshow button for a TV.
- Code: `worker/` (Worker + tests). The pages are rendered from the same templates by
  `tools/build_worker.py`. Deployment is done by the ACE OPS platform's **Event sites** workflow,
  which pins the commit to deploy in `events/daelin-nikita.ref`.

### One-time setup (about 10 minutes)

**A. Google Cloud (Ace, on any Google account):**
1. https://console.cloud.google.com → create a project named `Baby Shower Photos`.
2. **APIs & Services → Library** → search **Photos Library API** → **Enable**.
3. **Google Auth Platform → Branding**: app name `Daelin & Nikita Baby Shower`, add your support email → Save.
4. **Audience**: User type **External** → **Publish app** (status must say *In production*.
   In *Testing*, Google expires the sign-in after 7 days).
5. **Data Access → Add or remove scopes**: add `.../auth/photoslibrary.appendonly` and
   `.../auth/photoslibrary.readonly.appcreateddata` → Save.
6. **Clients → Create client → Web application**. Under *Authorized redirect URIs* add
   `https://daelin-nikita.aceops.workers.dev/auth/callback` → Create, then copy the **Client ID** and **Client secret**.

**B. Host desk (`/host`):**
1. Sign in with the **setup code**. Ace has it; it's never stored in this repo.
2. Paste the Client ID and secret → **Save client**.
3. Tap **Connect Google Photos** and sign in with **the parents' Google account**. Google shows
   *"Google hasn't verified this app"*, which is normal for a private family app: **Advanced → Go to … (unsafe)** → tick the boxes → Continue.
   The site can only add to, and read back, the album it creates. It can't see the rest of the library.
4. Set a **hosts' PIN** and give it to the hosts. They use it to open the desk.
5. Afterwards, share the album from the Google Photos app (**Share → Create link**). Google no longer lets websites do this.

Free-tier limits: Google Photos uploads count toward the parents' 15 GB of Google storage.
Cloudflare's free tier (100k requests/day) covers hundreds of guests.

---

### Fallback: the original Flask app

The Python app in this repo (`app.py`) still works on its own, with photos stored on its own disk.
It's no longer used for the live site.

#### Option A: PythonAnywhere (free, persistent disk, no card)
1. Sign up for a free **Beginner** account at <https://www.pythonanywhere.com>.
   The username becomes the address, so pick one like `daelinnikita` for
   `daelinnikita.pythonanywhere.com`.
2. *Consoles → Bash*:
   ```bash
   git clone https://github.com/waizyk/daelin-nikita-baby-shower.git
   cd daelin-nikita-baby-shower
   pip3.12 install --user -r requirements.txt
   python3 -c "import secrets; print(secrets.token_urlsafe(48))"   # copy this for SECRET_KEY
   ```
3. *Web → Add a new web app → Next → **Manual configuration** → Python 3.12*.
4. On the Web tab, open the **WSGI configuration file** link, delete everything and paste:
   ```python
   import os, sys
   os.environ["HOST_PIN"] = "PIN-ONLY-THE-HOSTS-KNOW"
   os.environ["SECRET_KEY"] = "PASTE-THE-LONG-RANDOM-STRING"
   os.environ["PUBLIC_URL"] = "https://YOURUSER.pythonanywhere.com"
   path = "/home/YOURUSER/daelin-nikita-baby-shower"
   if path not in sys.path:
       sys.path.insert(0, path)
   from app import app as application
   ```
   This file lives on PythonAnywhere only, not in the repo, so the secrets stay private.
5. Web tab → *Static files*: URL `/static/`, directory
   `/home/YOURUSER/daelin-nikita-baby-shower/static/`.
6. Web tab → *Security* → **Force HTTPS: on**. Click **Reload**.
7. Free web apps go to sleep after **1 month** unless you log in and click
   *Run until 1 month from today* on the Web tab. Do it on the morning of the
   shower, and again afterwards if you want the album to stay up.
8. Updates later: `cd ~/daelin-nikita-baby-shower && git pull`, then **Reload**.

Free-tier limits: 512 MB disk is roughly 500 photos at the sizes this app stores
(the code and libraries use some of it), and there's 1 web worker, so a big batch
from one guest makes the next guest wait a few seconds. Download the approved
zip from the host desk after the party (and once mid-way if it gets busy).

#### Option B: Render Blueprint (paid: starter plan + 1 GB disk)
`render.yaml` defines the service, a 1 GB disk on `data/`, a generated
`SECRET_KEY`, and prompts for `HOST_PIN` / `PUBLIC_URL`. *New → Blueprint → pick
this repo → Apply*. Render's free plan has no disk, so photos would be wiped
on every restart. Don't use it for this.

#### Option C: a laptop / Raspberry Pi on the venue wifi
```bash
pip install -r requirements.txt
HOST_PIN=... SECRET_KEY=... gunicorn app:app -b 0.0.0.0:8000 --workers 2
```
(Without HTTPS, leave `SECRET_KEY` unset or the host cookie won't stick, and
set the PIN with `HOST_PIN` only.)

> **Do this before printing:** open `/sign` **on the final domain** and hit
> *Print this sign*. Never print it from localhost or the GitHub Pages preview.

### GitHub Pages preview
`docs/` is a static copy of the invitation, album and upload pages for sharing
the look before launch: `https://waizyk.github.io/daelin-nikita-baby-shower/`.
It can't take uploads. Once the app is live, set `LIVE_URL` in
`tools/build_preview.py`, run it and commit, and the preview's buttons and QR will
point at the real site.

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
- Pending thumbnails are private too (`/media/thumb/<id>.jpg` returns 404 until approved).
- The host session is a signed cookie. `HOST_PIN` and `SECRET_KEY` come from
  environment variables (section 3) and are never committed.
- Want photos visible immediately (no approval)? Set `"auto_approve": True`.

---

## 6. Files

```
daelin-nikita-baby-shower/
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
├── tests/               pytest suite (run: python -m pytest -q)
├── tools/build_preview.py  builds docs/ (GitHub Pages static preview)
├── docs/                the generated preview, served by GitHub Pages
├── .github/workflows/ci.yml  tests + "docs/ is up to date" check on every push
├── render.yaml / Procfile / Dockerfile  hosting configs
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
