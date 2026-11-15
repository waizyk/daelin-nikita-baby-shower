// Pure helpers (kept out of index.js: a Worker entry module may only export its handler).

export const LIMITS = {
  chunk: 8 * 1024 * 1024,          // multiple of Google's 256 KiB granularity
  photo: 200 * 1024 * 1024,        // Google Photos' own limit for photos
  video: 2 * 1024 * 1024 * 1024,   // 2 GB per video (10+ minutes of 4K phone video)
  kvChunk: 20 * 1024 * 1024,       // site-storage pieces (KV values may be up to 25 MiB)
  kv_photo: 60 * 1024 * 1024,      // while photos are kept on the site (before Google Photos is connected)
  kv_video: 150 * 1024 * 1024,
  startsPerHour: 2000,     // per IP: guests on the venue Wi-Fi all share one IP              // per IP: a whole camera roll is fine, a script is not
};
const VIDEO_EXT = /\.(mp4|mov|m4v|3gp|webm|avi|mkv|mts)$/i;
const PHOTO_EXT = /\.(jpe?g|png|gif|webp|heic|heif|avif|bmp|tiff?|dng)$/i;

export function kindOf(type, name) {
  type = String(type || "").toLowerCase();
  name = String(name || "");
  const generic = !type || type === "application/octet-stream";
  if (type.startsWith("video/") || (generic && VIDEO_EXT.test(name))) return "video";
  if (type.startsWith("image/") || (generic && PHOTO_EXT.test(name))) return "photo";
  return null;
}

export function mimeFor(type, name, kind) {
  if (type && type !== "application/octet-stream") return type;
  const ext = (name.match(/\.([a-z0-9]+)$/i) || [])[1]?.toLowerCase();
  const map = { heic: "image/heic", heif: "image/heif", jpg: "image/jpeg", jpeg: "image/jpeg", png: "image/png",
    mov: "video/quicktime", mp4: "video/mp4", m4v: "video/mp4", webm: "video/webm", "3gp": "video/3gpp" };
  return map[ext] || (kind === "video" ? "video/mp4" : "image/jpeg");
}

export function clean(s, max) {
  return String(s || "").replace(/[\u0000-\u001f\u007f]/g, " ").replace(/\s+/g, " ").trim().slice(0, max);
}

export async function sha256(s) {
  const d = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(s));
  return [...new Uint8Array(d)].map(b => b.toString(16).padStart(2, "0")).join("");
}
