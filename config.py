import os

# ---------------------------------------------------------------------------
#  EDIT EVERYTHING IN THIS FILE  —  this is the only file you need to touch.
#  Anything left as [square brackets] is a placeholder and will show on the
#  page in a soft highlight so you can spot it before sharing the link.
# ---------------------------------------------------------------------------

EVENT = {
    # --- the parents-to-be -------------------------------------------------
    "parents": "Daelin & Nikita",
    "baby_surname": "Little One",         # e.g. "Baby Smith" — leave as is if unsure
    "tagline": "A little something is on the way",

    # --- when --------------------------------------------------------------
    "date_long": "Saturday, 5 December 2026",
    "date_short": "05 . 12 . 2026",
    "time": "2:00 pm",

    # --- where -------------------------------------------------------------
    "venue": "124 View Street",
    "address": "Overport, Durban",
    "map_url": "https://www.google.com/maps/search/?api=1&query=124+View+Street%2C+Overport%2C+Durban",

    # --- party details -----------------------------------------------------
    "theme": "Winnie the Pooh — a Hundred Acre Wood baby shower",
    "dress_code": "[Dress code — or delete this line]",
    "hosts": "[Host name(s)]",
    "rsvp": "[RSVP name & number]",       # e.g. "Thandi 082 123 4567"
    "rsvp_by": "[RSVP by date]",

    # --- gifts -------------------------------------------------------------
    "registry_url": "",                   # optional registry / gift list link
    "registry_note": "Your presence is the present — but if you'd like to spoil "
                     "our little one, a book for the nursery bookshelf would be lovely.",

    # --- Google Drive backup folder (already created for you) --------------
    "drive_url": "https://drive.google.com/drive/folders/1txOYJtkn1biaCc_c3_cRpNh6AzN9lUM4",

    # --- photo moderation ----------------------------------------------
    "auto_approve": False,                # True = photos appear instantly (not recommended)
    "max_photos_per_guest": 12,           # per upload batch
}

# ---------------------------------------------------------------------------
#  SECRETS — set these as ENVIRONMENT VARIABLES on the server, never here.
#  The fallbacks below are for running on your own laptop only. When the site
#  runs in production (SECRET_KEY set in the environment) the host desk
#  refuses to open with the default PIN.
# ---------------------------------------------------------------------------
DEFAULT_PIN = "hundredacre"
HOST_PIN = os.environ.get("HOST_PIN") or DEFAULT_PIN
SECRET_KEY = os.environ.get("SECRET_KEY") or "dev-only-not-secret-9f2b71"
PRODUCTION = bool(os.environ.get("SECRET_KEY"))

# Optional: the final public address, e.g. "https://daelinnikita.pythonanywhere.com".
# When set, every QR code uses it; when empty, QR codes follow whatever host
# the page is served from.
PUBLIC_URL = os.environ.get("PUBLIC_URL", "").rstrip("/")

# Shown in the site footer / page titles.
SITE_NAME = "Daelin & Nikita's Baby Shower"
