// Daelin & Nikita · baby shower photo site — Cloudflare Worker (free plan)
// Website by Ace Khan · ACE OPS
//
// Guests upload photos & videos  →  this Worker streams them (in 8 MB chunks) straight into an
// album in the parents' Google Photos (the permanent archive, original quality).
// A small D1 database keeps the index: who sent what, the message, and whether a host approved it.
// The public scrapbook only ever shows approved items; images are served from Google Photos.
//
// Pages (/, /photos, /upload, /host, /sign) are static files rendered from the Flask templates by
// tools/build_worker.py. Everything dynamic lives under /api/*, /m/* and /auth/*.

import { LIMITS, kindOf, mimeFor, sha256, clean } from "./lib.js";

const SCOPES = [
  "https://www.googleapis.com/auth/photoslibrary.appendonly",            // upload + add to our album
  "https://www.googleapis.com/auth/photoslibrary.readonly.appcreateddata", // read back what WE uploaded
];
const GOOGLE_AUTH = "https://accounts.google.com/o/oauth2/v2/auth";
const GOOGLE_TOKEN = "https://oauth2.googleapis.com/token";
const PHOTOS_API = "https://photoslibrary.googleapis.com";


const g = (env, k, d) => (env && env[k]) || d;
const now = () => Math.floor(Date.now() / 1000);

// ---------------------------------------------------------------- responses
const json = (data, status = 200, headers = {}) =>
  new Response(JSON.stringify(data), {
    status,
    headers: { "content-type": "application/json; charset=utf-8", "cache-control": "no-store", ...headers },
  });
const bad = (msg, status = 400) => json({ error: msg }, status);

// ---------------------------------------------------------------- database
const SCHEMA = [
  "CREATE TABLE IF NOT EXISTS settings (k TEXT PRIMARY KEY, v TEXT NOT NULL)",
  `CREATE TABLE IF NOT EXISTS media (
     id TEXT PRIMARY KEY, gp_id TEXT, kind TEXT NOT NULL, mime TEXT, name TEXT,
     guest TEXT, message TEXT, status TEXT NOT NULL DEFAULT 'pending', created INTEGER NOT NULL,
     w INTEGER, h INTEGER, ready INTEGER NOT NULL DEFAULT 1, base_url TEXT, base_exp INTEGER NOT NULL DEFAULT 0,
     size INTEGER)`,
  "CREATE INDEX IF NOT EXISTS media_status ON media(status, created)",
  `CREATE TABLE IF NOT EXISTS uploads (
     id TEXT PRIMARY KEY, url TEXT NOT NULL, size INTEGER NOT NULL, mime TEXT, kind TEXT, name TEXT,
     guest TEXT, message TEXT, ip TEXT, created INTEGER NOT NULL, media_id TEXT)`,
  "CREATE TABLE IF NOT EXISTS hits (k TEXT NOT NULL, ts INTEGER NOT NULL)",
  "CREATE INDEX IF NOT EXISTS hits_k ON hits(k, ts)",
];
// Columns added after the first deploy (ALTER is a no-op error if they already exist).
const COLUMNS = {
  media: ["store TEXT NOT NULL DEFAULT 'google'", "chunk INTEGER", "thumb TEXT"],
  uploads: ["store TEXT NOT NULL DEFAULT 'google'", "chunk INTEGER", "got INTEGER NOT NULL DEFAULT 0", "thumb TEXT", "w INTEGER", "h INTEGER"],
};
let schemaReady = false;
async function ensureSchema(env) {
  if (schemaReady) return;
  await env.DB.batch(SCHEMA.map(s => env.DB.prepare(s)));
  for (const [table, cols] of Object.entries(COLUMNS)) {
    const { results } = await env.DB.prepare(`PRAGMA table_info(${table})`).all();
    const have = new Set(results.map(r => r.name));
    for (const c of cols) if (!have.has(c.split(" ")[0])) await env.DB.prepare(`ALTER TABLE ${table} ADD COLUMN ${c}`).run();
  }
  schemaReady = true;
}

// Where new uploads go: Google Photos once connected, otherwise the site's own Cloudflare storage
// (KV, free tier 1 GB). Stored items are copied into Google Photos from the host desk later.
async function uploadMode(env) {
  const s = await getS(env, "refresh_token", "album_id", "needs_reconnect", "paused");
  if (s.paused) return { mode: null, why: "paused" };
  if (s.refresh_token && s.album_id && !s.needs_reconnect) return { mode: "google" };
  if (!env.MEDIA) return { mode: null, why: "closed" };
  return { mode: "kv" };
}
async function kvUsage(env) {
  const r = await env.DB.prepare(
    "SELECT COALESCE(SUM(size),0) AS bytes, COUNT(*) AS n FROM media WHERE store = 'kv'").first();
  const p = await env.DB.prepare(
    "SELECT COALESCE(SUM(size),0) AS bytes FROM uploads WHERE store = 'kv' AND media_id IS NULL AND created > ?").bind(now() - 3600).first();
  return { bytes: r.bytes + p.bytes, n: r.n, budget: +g(env, "KV_BUDGET_MB", "900") * 1024 * 1024 };
}
const kvKey = (uploadId, i) => `m:${uploadId}:${i}`;

async function getS(env, ...keys) {
  const { results } = await env.DB.prepare(
    `SELECT k, v FROM settings WHERE k IN (${keys.map(() => "?").join(",")})`).bind(...keys).all();
  const o = {};
  for (const r of results) o[r.k] = r.v;
  return o;
}
async function setS(env, obj) {
  const st = env.DB.prepare("INSERT INTO settings (k, v) VALUES (?, ?) ON CONFLICT(k) DO UPDATE SET v = excluded.v");
  const del = env.DB.prepare("DELETE FROM settings WHERE k = ?");
  await env.DB.batch(Object.entries(obj).map(([k, v]) => (v === null ? del.bind(k) : st.bind(k, String(v)))));
}

// Simple sliding-window rate limit kept in D1.
async function limited(env, key, max, windowSec) {
  const since = now() - windowSec;
  const row = await env.DB.prepare("SELECT COUNT(*) AS n FROM hits WHERE k = ? AND ts > ?").bind(key, since).first();
  if (row.n >= max) return true;
  await env.DB.batch([
    env.DB.prepare("INSERT INTO hits (k, ts) VALUES (?, ?)").bind(key, now()),
    env.DB.prepare("DELETE FROM hits WHERE ts < ?").bind(now() - 86400),
  ]);
  return false;
}

// ---------------------------------------------------------------- crypto helpers
const enc = new TextEncoder();
const hex = buf => [...new Uint8Array(buf)].map(b => b.toString(16).padStart(2, "0")).join("");
const randomId = (bytes = 12) => hex(crypto.getRandomValues(new Uint8Array(bytes)).buffer);
async function hmac(secret, msg) {
  const key = await crypto.subtle.importKey("raw", enc.encode(secret), { name: "HMAC", hash: "SHA-256" }, false, ["sign"]);
  return hex(await crypto.subtle.sign("HMAC", key, enc.encode(msg)));
}
function same(a, b) {               // constant-time string compare
  if (typeof a !== "string" || typeof b !== "string" || a.length !== b.length) return false;
  let x = 0;
  for (let i = 0; i < a.length; i++) x |= a.charCodeAt(i) ^ b.charCodeAt(i);
  return x === 0;
}
async function pinHash(pin, salt) {
  const key = await crypto.subtle.importKey("raw", enc.encode(pin), "PBKDF2", false, ["deriveBits"]);
  const bits = await crypto.subtle.deriveBits({ name: "PBKDF2", hash: "SHA-256", salt: enc.encode(salt), iterations: 5000 }, key, 256);
  return hex(bits);
}
async function secret(env) {
  let { session_secret: s } = await getS(env, "session_secret");
  if (!s) { s = randomId(32); await setS(env, { session_secret: s }); }
  return s;
}

// ---------------------------------------------------------------- host sessions
function cookie(req, name) {
  const m = (req.headers.get("cookie") || "").match(new RegExp("(?:^|;\\s*)" + name + "=([^;]+)"));
  return m ? decodeURIComponent(m[1]) : null;
}
const cookieAttrs = req => (new URL(req.url).protocol === "https:" ? "; Secure" : "") + "; HttpOnly; SameSite=Lax; Path=/";

async function makeSession(env, role) {
  const { pin_v = "0" } = await getS(env, "pin_v");
  const exp = now() + 30 * 86400;
  const body = `${role}.${pin_v}.${exp}`;
  return `${body}.${await hmac(await secret(env), body)}`;
}
async function hostRole(req, env) {
  const c = cookie(req, "host");
  if (!c) return null;
  const parts = c.split(".");
  if (parts.length !== 4) return null;
  const [role, pv, exp, sig] = parts;
  if (!same(sig, await hmac(await secret(env), `${role}.${pv}.${exp}`))) return null;
  if (+exp < now()) return null;
  if (role === "host") {                     // helpers are logged out when the PIN changes
    const { pin_v = "0" } = await getS(env, "pin_v");
    if (pv !== pin_v) return null;
  }
  return role === "admin" || role === "host" ? role : null;
}

// ---------------------------------------------------------------- Google
async function accessToken(env) {
  const s = await getS(env, "access_token", "access_exp", "refresh_token", "client_id", "client_secret");
  if (!s.refresh_token) throw new HttpError(503, "The album isn't connected to Google Photos yet.");
  if (s.access_token && +s.access_exp > now() + 120) return s.access_token;
  const r = await fetch(g(env, "GOOGLE_TOKEN_URL", GOOGLE_TOKEN), {
    method: "POST",
    headers: { "content-type": "application/x-www-form-urlencoded" },
    body: new URLSearchParams({
      client_id: s.client_id, client_secret: s.client_secret,
      refresh_token: s.refresh_token, grant_type: "refresh_token",
    }),
  });
  const d = await r.json().catch(() => ({}));
  if (!r.ok || !d.access_token) {
    if (d.error === "invalid_grant") await setS(env, { needs_reconnect: "1" });
    throw new HttpError(503, "The album's Google Photos connection needs a host to reconnect it.");
  }
  await setS(env, { access_token: d.access_token, access_exp: now() + (d.expires_in || 3600), needs_reconnect: null });
  return d.access_token;
}

async function photos(env, path, opts = {}) {
  const tok = await accessToken(env);
  const r = await fetch(g(env, "PHOTOS_API", PHOTOS_API) + path, {
    ...opts,
    headers: { authorization: `Bearer ${tok}`, "content-type": "application/json", ...(opts.headers || {}) },
  });
  const d = await r.json().catch(() => ({}));
  if (!r.ok) throw new HttpError(502, (d.error && d.error.message) || `Google Photos said ${r.status}`);
  return d;
}

async function createAlbum(env) {
  const title = g(env, "ALBUM_TITLE", "Baby Shower");
  const a = await photos(env, "/v1/albums", { method: "POST", body: JSON.stringify({ album: { title } }) });
  await setS(env, { album_id: a.id, album_url: a.productUrl || "" });
  return a.id;
}

class HttpError extends Error {
  constructor(status, msg) { super(msg); this.status = status; }
}

// Fetch fresh baseUrls (they last 60 min) for one item plus any other stale approved items, in one call.
async function refreshBase(env, item) {
  const others = await env.DB.prepare(
    "SELECT gp_id FROM media WHERE status = 'approved' AND gp_id != ? AND base_exp < ? ORDER BY created DESC LIMIT 49")
    .bind(item.gp_id, now() + 900).all();
  const ids = [item.gp_id, ...others.results.map(r => r.gp_id)];
  const q = ids.map(i => "mediaItemIds=" + encodeURIComponent(i)).join("&");
  const d = await photos(env, "/v1/mediaItems:batchGet?" + q, { method: "GET" });
  const exp = now() + 50 * 60;
  const upd = env.DB.prepare("UPDATE media SET base_url = ?, base_exp = ?, ready = ?, w = COALESCE(?, w), h = COALESCE(?, h) WHERE gp_id = ?");
  const stmts = [];
  let mine = null;
  for (const res of d.mediaItemResults || []) {
    const m = res.mediaItem;
    if (!m || !m.baseUrl) continue;
    const md = m.mediaMetadata || {};
    const ready = md.video ? (md.video.status === "READY" ? 1 : 0) : 1;
    stmts.push(upd.bind(m.baseUrl, ready ? exp : now() + 60, ready, +md.width || null, +md.height || null, m.id));
    if (m.id === item.gp_id) mine = { base_url: m.baseUrl, ready };
  }
  if (stmts.length) await env.DB.batch(stmts);
  return mine;
}

// ---------------------------------------------------------------- helpers
const ip = req => req.headers.get("cf-connecting-ip") || "local";
const publicItem = m => ({ id: m.id, kind: m.kind, guest: m.guest, message: m.message, w: m.w, h: m.h, ready: m.ready, t: m.created });

async function readJSON(req) {
  try { return await req.json(); } catch { throw new HttpError(400, "Bad request."); }
}
function requireXHR(req) {        // blocks cross-site form posts (the browser preflights custom headers)
  if (req.headers.get("x-requested-with") !== "fetch") throw new HttpError(403, "Forbidden.");
}

// ---------------------------------------------------------------- routes
async function route(req, env) {
  const url = new URL(req.url);
  const p = url.pathname;
  const M = req.method;

  if (!(p.startsWith("/api/") || p.startsWith("/m/") || p.startsWith("/auth/"))) {
    return env.ASSETS.fetch(req);
  }
  await ensureSchema(env);

  // ---- public
  if (p === "/api/health") return json({ ok: true });

  if (p === "/api/status" && M === "GET") {
    const s = await getS(env, "refresh_token", "needs_reconnect", "album_id", "paused");
    const c = await env.DB.prepare("SELECT kind, COUNT(*) AS n FROM media WHERE status = 'approved' GROUP BY kind").all();
    const counts = { photo: 0, video: 0 };
    for (const r of c.results) counts[r.kind] = r.n;
    const { mode } = await uploadMode(env);
    return json({ accepting: !!mode, mode, counts });
  }

  if (p === "/api/photos" && M === "GET") {
    const { results } = await env.DB.prepare(
      "SELECT id, kind, guest, message, w, h, ready, created FROM media WHERE status = 'approved' ORDER BY created DESC LIMIT 2000").all();
    return json({ items: results.map(publicItem) });
  }

  // ---- uploads
  if (p === "/api/upload/start" && M === "POST") {
    requireXHR(req);
    const b = await readJSON(req);
    const name = clean(b.name, 180) || "photo";
    const size = Math.floor(+b.size || 0);
    const kind = kindOf(b.type, name);
    if (!kind) return bad("Only photos and videos can go in the album.");
    if (size <= 0) return bad("That file looks empty.");
    const { mode } = await uploadMode(env);
    if (!mode) return bad("The album isn't taking uploads right now. Please use the Google Drive link below.", 503);
    const max = mode === "kv" ? LIMITS["kv_" + kind] : LIMITS[kind];
    if (size > max) return bad(kind === "video"
      ? `That video is over ${Math.round(max / 1048576)} MB. Please trim it, or use the Google Drive link below.`
      : "That photo is too big for the album.", 413);
    if (await limited(env, "start:" + ip(req), LIMITS.startsPerHour, 3600))
      return bad("That's a lot of uploads in one go! Give it a few minutes and try again.", 429);

    const mime = mimeFor(b.type, name, kind);
    const thumb = typeof b.thumb === "string" && /^data:image\/jpeg;base64,[A-Za-z0-9+/=]+$/.test(b.thumb) && b.thumb.length < 140000
      ? b.thumb.slice(23) : null;
    const dims = [Math.floor(+b.w) || null, Math.floor(+b.h) || null];
    const id = randomId();
    if (mode === "kv") {
      const use = await kvUsage(env);
      if (use.bytes + size > use.budget)
        return bad("The website's photo storage is full. Please use the Google Drive link below.", 507);
      await env.DB.prepare(
        `INSERT INTO uploads (id, url, size, mime, kind, name, guest, message, ip, created, store, chunk, thumb, w, h)
         VALUES (?,?,?,?,?,?,?,?,?,?,'kv',?,?,?,?)`)
        .bind(id, "", size, mime, kind, name, clean(b.guest, 60), clean(b.message, 300), ip(req), now(),
              LIMITS.kvChunk, thumb, dims[0], dims[1]).run();
      return json({ id, chunk: LIMITS.kvChunk, mode });
    }
    const tok = await accessToken(env);
    const r = await fetch(g(env, "PHOTOS_API", PHOTOS_API) + "/v1/uploads", {
      method: "POST",
      body: "",
      headers: {
        authorization: `Bearer ${tok}`,
        "x-goog-upload-command": "start",
        "x-goog-upload-content-type": mime,
        "x-goog-upload-protocol": "resumable",
        "x-goog-upload-raw-size": String(size),
      },
    });
    const upUrl = r.headers.get("x-goog-upload-url");
    if (!r.ok || !upUrl) throw new HttpError(502, "Google Photos didn't accept the upload — please try again.");
    const gran = +r.headers.get("x-goog-upload-chunk-granularity") || 262144;
    const chunk = Math.max(gran, Math.floor(LIMITS.chunk / gran) * gran);
    await env.DB.prepare(
      "INSERT INTO uploads (id, url, size, mime, kind, name, guest, message, ip, created, thumb, w, h) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)")
      .bind(id, upUrl, size, mime, kind, name, clean(b.guest, 60), clean(b.message, 300), ip(req), now(), thumb, dims[0], dims[1]).run();
    return json({ id, chunk, mode });
  }

  let m;
  if ((m = p.match(/^\/api\/upload\/([a-f0-9]{24})$/))) {
    const up = await env.DB.prepare("SELECT * FROM uploads WHERE id = ?").bind(m[1]).first();
    if (!up || up.created < now() - 86400) return bad("This upload expired — please add the file again.", 410);
    if (up.media_id) return json({ ok: true, done: true, received: up.size });

    if (up.store === "kv") return kvChunk(req, env, url, up, M);

    if (M === "GET") {                                   // resume: how much did Google get?
      const r = await fetch(up.url, { method: "POST", body: "", headers: { "x-goog-upload-command": "query" } });
      return json({ received: +r.headers.get("x-goog-upload-size-received") || 0, status: r.headers.get("x-goog-upload-status") });
    }
    if (M !== "PUT") return bad("Method not allowed.", 405);
    requireXHR(req);
    const offset = Math.floor(+url.searchParams.get("offset") || 0);
    const buf = await req.arrayBuffer();
    if (!buf.byteLength || buf.byteLength > LIMITS.chunk + 1024 || offset < 0 || offset + buf.byteLength > up.size)
      return bad("Bad chunk.");
    const final = offset + buf.byteLength === up.size;
    const r = await fetch(up.url, {
      method: "POST",
      body: buf,
      headers: { "x-goog-upload-command": final ? "upload, finalize" : "upload", "x-goog-upload-offset": String(offset) },
    });
    if (!r.ok) throw new HttpError(502, "Part of the file didn't arrive — retrying.");
    if (!final) return json({ ok: true, received: offset + buf.byteLength });

    const uploadToken = (await r.text()).trim();
    const media = await addToAlbum(env, up, uploadToken);
    return json({ ok: true, done: true, status: media.status });
  }

  // ---- media (served from Google Photos via short-lived redirects)
  if ((m = p.match(/^\/m\/([a-f0-9]{24})\/(t|f|v|d)$/)) && M === "GET") {
    const item = await env.DB.prepare("SELECT * FROM media WHERE id = ?").bind(m[1]).first();
    if (!item) return new Response("Not found", { status: 404 });
    if (item.status !== "approved" && !(await hostRole(req, env))) return new Response("Not found", { status: 404 });
    const priv = item.status === "approved" ? "public, max-age=86400" : "private, no-store";
    if (m[2] === "t" && item.thumb) {
      const bin = atob(item.thumb), bytes = new Uint8Array(bin.length);
      for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
      return new Response(bytes, { headers: { "content-type": "image/jpeg", "cache-control": priv } });
    }
    if (item.store === "kv") {
      if (m[2] === "t") return new Response("No preview", { status: 404 });
      return serveKV(req, env, item, m[2] === "d", priv);
    }
    if (!item.gp_id) return new Response("Not found", { status: 404 });
    let base = item.base_exp > now() + 600 ? { base_url: item.base_url, ready: item.ready } : await refreshBase(env, item);
    if (!base || !base.base_url) return new Response("Not found", { status: 404 });
    if (item.kind === "video" && !base.ready && m[2] !== "t")
      return new Response("Still processing", { status: 409, headers: { "retry-after": "30" } });
    const suffix = { t: "=w720-h720", f: "=w2048-h2048", v: "=dv", d: item.kind === "video" ? "=dv" : "=d" }[m[2]];
    return new Response(null, {
      status: 302,
      headers: { location: base.base_url + suffix, "cache-control": "private, max-age=1500", "referrer-policy": "no-referrer" },
    });
  }

  // ---- host desk
  if (p === "/api/host/login" && M === "POST") {
    requireXHR(req);
    if (await limited(env, "login:" + ip(req), LIMITS.loginsPer15m, 900))
      return bad("Too many tries — wait 15 minutes and try again.", 429);
    const { code } = await readJSON(req);
    const c = String(code || "").trim();
    let role = null;
    if (env.SETUP_HASH && c && same(await sha256(c.toLowerCase()), env.SETUP_HASH)) role = "admin";
    else {
      const s = await getS(env, "pin_hash", "pin_salt");
      if (s.pin_hash && c && same(await pinHash(c, s.pin_salt), s.pin_hash)) role = "host";
    }
    if (!role) return bad("That code didn't work.", 401);
    return json({ ok: true, role }, 200, { "set-cookie": `host=${await makeSession(env, role)}; Max-Age=${30 * 86400}${cookieAttrs(req)}` });
  }
  if (p === "/api/host/logout" && M === "POST") {
    return json({ ok: true }, 200, { "set-cookie": `host=; Max-Age=0${cookieAttrs(req)}` });
  }

  const role = await hostRole(req, env);
  if (p === "/api/host/state" && M === "GET") {
    if (!role) return json({ role: null });
    const s = await getS(env, "client_id", "refresh_token", "album_id", "album_url", "needs_reconnect", "pin_hash", "paused", "connected_at");
    const c = await env.DB.prepare("SELECT status, COUNT(*) AS n FROM media GROUP BY status").all();
    const counts = { pending: 0, approved: 0, hidden: 0 };
    for (const r of c.results) counts[r.status] = r.n;
    return json({
      role, counts,
      has_client: !!s.client_id,
      client_hint: s.client_id ? s.client_id.slice(0, 10) + "…" : "",
      connected: !!(s.refresh_token && s.album_id && !s.needs_reconnect),
      needs_reconnect: !!s.needs_reconnect,
      album_url: s.album_url || "",
      connected_at: s.connected_at ? +s.connected_at : null,
      has_pin: !!s.pin_hash,
      stored: await kvUsage(env),
      paused: !!s.paused,
      redirect_uri: url.origin + "/auth/callback",
    });
  }
  if (p.startsWith("/api/host/") && !role) return bad("Please sign in to the host desk.", 401);

  if (p === "/api/host/media" && M === "GET") {
    const status = ["pending", "approved", "hidden"].includes(url.searchParams.get("status")) ? url.searchParams.get("status") : "pending";
    const { results } = await env.DB.prepare(
      "SELECT id, kind, guest, message, w, h, ready, created, size, name FROM media WHERE status = ? ORDER BY created DESC LIMIT 1000")
      .bind(status).all();
    return json({ items: results.map(r => ({ ...publicItem(r), size: r.size, name: r.name })) });
  }
  if (p === "/api/host/media" && M === "POST") {
    requireXHR(req);
    const { ids, status } = await readJSON(req);
    if (!["pending", "approved", "hidden"].includes(status) || !Array.isArray(ids) || !ids.length || ids.length > 500)
      return bad("Bad request.");
    const st = env.DB.prepare("UPDATE media SET status = ? WHERE id = ?");
    await env.DB.batch(ids.filter(i => /^[a-f0-9]{24}$/.test(i)).map(i => st.bind(status, i)));
    return json({ ok: true });
  }
  if (p === "/api/host/migrate" && M === "POST") {
    requireXHR(req);
    const { mode } = await uploadMode(env);
    if (mode !== "google") return bad("Connect Google Photos first.", 409);
    const item = await env.DB.prepare("SELECT * FROM media WHERE store = 'kv' ORDER BY created LIMIT 1").first();
    if (item) await migrateOne(env, item);
    const left = await env.DB.prepare("SELECT COUNT(*) AS n FROM media WHERE store = 'kv'").first();
    return json({ ok: true, moved: item ? 1 : 0, left: left.n });
  }
  if (p === "/api/host/pin" && M === "POST") {
    requireXHR(req);
    const { pin } = await readJSON(req);
    const v = String(pin || "").trim();
    if (v.length < 6 || v.length > 64) return bad("Make the PIN 6 to 64 characters.");
    const salt = randomId(8);
    const { pin_v = "0" } = await getS(env, "pin_v");
    await setS(env, { pin_hash: await pinHash(v, salt), pin_salt: salt, pin_v: +pin_v + 1 });
    return json({ ok: true });
  }
  if (p === "/api/host/pause" && M === "POST") {
    requireXHR(req);
    const { paused } = await readJSON(req);
    await setS(env, { paused: paused ? "1" : null });
    return json({ ok: true });
  }
  if (p === "/api/host/google" && M === "POST") {
    requireXHR(req);
    if (role !== "admin") return bad("Only the site admin can change this.", 403);
    const b = await readJSON(req);
    const id = clean(b.client_id, 200), sec = clean(b.client_secret, 200);
    if (!/\.apps\.googleusercontent\.com$/.test(id) || sec.length < 10)
      return bad("That doesn't look like a Google OAuth client ID and secret.");
    await setS(env, { client_id: id, client_secret: sec, refresh_token: null, access_token: null, access_exp: null });
    return json({ ok: true });
  }

  // ---- Google sign-in (connect the album)
  if (p === "/auth/google" && M === "GET") {
    if (!role) return Response.redirect(url.origin + "/host", 302);
    const { client_id } = await getS(env, "client_id");
    if (!client_id) return Response.redirect(url.origin + "/host?err=client", 302);
    const nonce = randomId(16);
    const auth = new URL(g(env, "GOOGLE_AUTH_URL", GOOGLE_AUTH));
    auth.search = new URLSearchParams({
      client_id, redirect_uri: url.origin + "/auth/callback", response_type: "code",
      scope: SCOPES.join(" "), access_type: "offline", prompt: "consent", state: nonce,
    }).toString();
    return new Response(null, {
      status: 302,
      headers: { location: auth.toString(), "set-cookie": `gstate=${nonce}; Max-Age=900${cookieAttrs(req)}` },
    });
  }
  if (p === "/auth/callback" && M === "GET") {
    const back = q => Response.redirect(url.origin + "/host?" + q, 302);
    if (!role) return back("err=session");
    const st = url.searchParams.get("state"), code = url.searchParams.get("code");
    if (url.searchParams.get("error")) return back("err=denied");
    if (!st || !same(st, cookie(req, "gstate") || "") || !code) return back("err=state");
    const s = await getS(env, "client_id", "client_secret", "album_id");
    const r = await fetch(g(env, "GOOGLE_TOKEN_URL", GOOGLE_TOKEN), {
      method: "POST",
      headers: { "content-type": "application/x-www-form-urlencoded" },
      body: new URLSearchParams({
        code, client_id: s.client_id, client_secret: s.client_secret,
        redirect_uri: url.origin + "/auth/callback", grant_type: "authorization_code",
      }),
    });
    const d = await r.json().catch(() => ({}));
    if (!r.ok || !d.refresh_token) return back("err=token");
    const granted = String(d.scope || "");
    if (!granted.includes("photoslibrary.appendonly")) return back("err=scope");
    await setS(env, {
      refresh_token: d.refresh_token, access_token: d.access_token, access_exp: now() + (d.expires_in || 3600),
      needs_reconnect: null, connected_at: now(),
    });
    if (!s.album_id) {
      try { await createAlbum(env); }
      catch (e) { console.error("album", e && e.message); return back("err=album"); }
    }
    const res = back("connected=1");
    const h = new Headers(res.headers);
    h.append("set-cookie", `gstate=; Max-Age=0${cookieAttrs(req)}`);
    return new Response(null, { status: 302, headers: h });
  }

  return bad("Not found.", 404);
}

// ---------------------------------------------------------------- site storage (KV)
async function kvChunk(req, env, url, up, M) {
  if (M === "GET") return json({ received: up.got });
  if (M !== "PUT") return bad("Method not allowed.", 405);
  requireXHR(req);
  const offset = Math.floor(+url.searchParams.get("offset") || 0);
  const buf = await req.arrayBuffer();
  const idx = offset / up.chunk;
  const expect = Math.min(up.chunk, up.size - offset);
  if (!Number.isInteger(idx) || offset < 0 || offset >= up.size || buf.byteLength !== expect) return bad("Bad chunk.");
  try {
    await env.MEDIA.put(kvKey(up.id, idx), buf);
  } catch (e) {
    console.error("kv put", e && e.message);
    throw new HttpError(507, "The website's photo storage is full for today. Please use the Google Drive link below.");
  }
  const got = offset + buf.byteLength;
  await env.DB.prepare("UPDATE uploads SET got = MAX(got, ?) WHERE id = ?").bind(got, up.id).run();
  if (got < up.size) return json({ ok: true, received: got });

  const id = randomId();
  const status = g(env, "AUTO_APPROVE", "") === "1" ? "approved" : "pending";
  await env.DB.batch([
    env.DB.prepare(`INSERT INTO media (id, gp_id, kind, mime, name, guest, message, status, created, w, h, ready, size, store, chunk, thumb, base_url)
                    VALUES (?,NULL,?,?,?,?,?,?,?,?,?,1,?,'kv',?,?,?)`)
      .bind(id, up.kind, up.mime, up.name, up.guest || "", up.message || "", status, now(), up.w, up.h, up.size, up.chunk, up.thumb, up.id),
    env.DB.prepare("UPDATE uploads SET media_id = ? WHERE id = ?").bind(id, up.id),
  ]);
  return json({ ok: true, done: true, status });
}

// Serve a stored file, with byte ranges (iPhones need them to play video).
async function serveKV(req, env, item, download, cacheControl) {
  const size = item.size, chunk = item.chunk, key = i => kvKey(item.base_url, i);  // base_url holds the upload id
  const n = Math.ceil(size / chunk);
  const head = {
    "content-type": item.mime || "application/octet-stream", "accept-ranges": "bytes", "cache-control": cacheControl,
    ...(download ? { "content-disposition": `attachment; filename="${(item.name || "photo").replace(/[^\w.\- ]+/g, "_")}"` } : {}),
  };
  const range = (req.headers.get("range") || "").match(/^bytes=(\d*)-(\d*)$/);
  if (range && (range[1] || range[2])) {
    let start, end;
    if (range[1] === "") { start = Math.max(0, size - +range[2]); end = size - 1; }
    else { start = +range[1]; end = range[2] === "" ? size - 1 : Math.min(+range[2], size - 1); }
    if (start >= size || start > end) return new Response(null, { status: 416, headers: { "content-range": `bytes */${size}` } });
    const i = Math.floor(start / chunk);
    end = Math.min(end, (i + 1) * chunk - 1);                    // one stored piece per response
    const buf = await env.MEDIA.get(key(i), "arrayBuffer");
    if (!buf) return new Response("Not found", { status: 404 });
    const part = buf.slice(start - i * chunk, end - i * chunk + 1);
    return new Response(part, { status: 206, headers: { ...head, "content-range": `bytes ${start}-${end}/${size}`, "content-length": String(part.byteLength) } });
  }
  if (n === 1) {
    const st = await env.MEDIA.get(key(0), "stream");
    if (!st) return new Response("Not found", { status: 404 });
    return new Response(st, { headers: { ...head, "content-length": String(size) } });
  }
  let i = 0;
  const body = new ReadableStream({
    async pull(ctl) {
      if (i >= n) return ctl.close();
      const b = await env.MEDIA.get(key(i++), "arrayBuffer");
      if (!b) return ctl.error(new Error("missing piece"));
      ctl.enqueue(new Uint8Array(b));
    },
  });
  return new Response(body, { headers: { ...head, "content-length": String(size) } });
}

// Copy one stored item into the Google Photos album, then free the site storage.
async function migrateOne(env, item) {
  const tok = await accessToken(env);
  const r = await fetch(g(env, "PHOTOS_API", PHOTOS_API) + "/v1/uploads", {
    method: "POST", body: "",
    headers: {
      authorization: `Bearer ${tok}`, "x-goog-upload-command": "start", "x-goog-upload-content-type": item.mime,
      "x-goog-upload-protocol": "resumable", "x-goog-upload-raw-size": String(item.size),
    },
  });
  const upUrl = r.headers.get("x-goog-upload-url");
  if (!r.ok || !upUrl) throw new HttpError(502, "Google Photos didn't accept the file. Try again in a minute.");
  const n = Math.ceil(item.size / item.chunk);
  let res;
  for (let i = 0; i < n; i++) {
    const buf = await env.MEDIA.get(kvKey(item.base_url, i), "arrayBuffer");
    if (!buf) throw new HttpError(500, "A stored piece of " + item.name + " is missing.");
    res = await fetch(upUrl, {
      method: "POST", body: buf,
      headers: { "x-goog-upload-command": i === n - 1 ? "upload, finalize" : "upload", "x-goog-upload-offset": String(i * item.chunk) },
    });
    if (!res.ok) throw new HttpError(502, "Google Photos dropped the upload. Try again.");
  }
  const uploadToken = (await res.text()).trim();
  const created = await createMediaItem(env, item, uploadToken);
  const keys = Array.from({ length: n }, (_, i) => kvKey(item.base_url, i));
  await env.DB.prepare("UPDATE media SET gp_id = ?, store = 'google', base_url = NULL, base_exp = 0, ready = ? WHERE id = ?")
    .bind(created.id, created.ready, item.id).run();
  for (const k of keys) await env.MEDIA.delete(k);
}

async function createMediaItem(env, up, uploadToken) {
  const desc = (up.guest ? `From ${up.guest}` : "From a guest") + (up.message ? ` — “${up.message}”` : "");
  let { album_id } = await getS(env, "album_id");
  const create = albumId => photos(env, "/v1/mediaItems:batchCreate", {
    method: "POST",
    body: JSON.stringify({
      ...(albumId ? { albumId } : {}),
      newMediaItems: [{ description: desc.slice(0, 1000), simpleMediaItem: { uploadToken, fileName: up.name } }],
    }),
  });
  let d;
  try { d = await create(album_id); }
  catch (e) { album_id = await createAlbum(env); d = await create(album_id); }
  const res = (d.newMediaItemResults || [])[0] || {};
  const item = res.mediaItem;
  if (!item || !item.id) throw new HttpError(502, (res.status && res.status.message) || "Google Photos couldn't save that file.");
  const md = item.mediaMetadata || {};
  return { id: item.id, md, ready: up.kind === "video" ? (md.video && md.video.status === "READY" ? 1 : 0) : 1 };
}

async function addToAlbum(env, up, uploadToken) {
  const desc = (up.guest ? `From ${up.guest}` : "From a guest") + (up.message ? ` — “${up.message}”` : "");
  let { album_id } = await getS(env, "album_id");
  const create = albumId => photos(env, "/v1/mediaItems:batchCreate", {
    method: "POST",
    body: JSON.stringify({
      ...(albumId ? { albumId } : {}),
      newMediaItems: [{ description: desc.slice(0, 1000), simpleMediaItem: { uploadToken, fileName: up.name } }],
    }),
  });
  let d;
  try { d = await create(album_id); }
  catch (e) {                                    // album deleted in the Photos app? make a fresh one
    album_id = await createAlbum(env);
    d = await create(album_id);
  }
  const res = (d.newMediaItemResults || [])[0] || {};
  const item = res.mediaItem;
  if (!item || !item.id) throw new HttpError(502, (res.status && res.status.message) || "Google Photos couldn't save that file.");
  const md = item.mediaMetadata || {};
  const id = randomId();
  const status = g(env, "AUTO_APPROVE", "") === "1" ? "approved" : "pending";
  const ready = up.kind === "video" ? (md.video && md.video.status === "READY" ? 1 : 0) : 1;
  await env.DB.batch([
    env.DB.prepare(`INSERT INTO media (id, gp_id, kind, mime, name, guest, message, status, created, w, h, ready, size, thumb)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)`)
      .bind(id, item.id, up.kind, up.mime, up.name, up.guest || "", up.message || "", status, now(),
            +md.width || up.w || null, +md.height || up.h || null, ready, up.size, up.thumb || null),
    env.DB.prepare("UPDATE uploads SET media_id = ?, url = '' WHERE id = ?").bind(id, up.id),
  ]);
  return { id, status };
}

export default {
  async fetch(req, env, ctx) {
    try {
      return await route(req, env, ctx);
    } catch (e) {
      if (e instanceof HttpError) return bad(e.message, e.status);
      console.error(e && e.stack || e);
      return bad("Something went wrong on our side — please try again.", 500);
    }
  },
};
