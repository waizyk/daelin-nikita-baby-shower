import test from "node:test";
import assert from "node:assert/strict";
import { kindOf, mimeFor, clean, LIMITS, sha256 } from "../src/lib.js";

test("photos and videos are recognised, other files are refused", () => {
  assert.equal(kindOf("image/jpeg", "a.jpg"), "photo");
  assert.equal(kindOf("", "IMG_0001.HEIC"), "photo");            // iPhone HEIC often has no type
  assert.equal(kindOf("application/octet-stream", "clip.MOV"), "video");
  assert.equal(kindOf("video/quicktime", "x"), "video");
  assert.equal(kindOf("application/pdf", "menu.pdf"), null);
  assert.equal(kindOf("", "virus.exe"), null);
});

test("a sensible mime type is always sent to Google", () => {
  assert.equal(mimeFor("", "IMG_1.heic", "photo"), "image/heic");
  assert.equal(mimeFor("application/octet-stream", "v.mov", "video"), "video/quicktime");
  assert.equal(mimeFor("image/png", "x.png", "photo"), "image/png");
  assert.equal(mimeFor("", "noext", "video"), "video/mp4");
});

test("names and messages are tidied and capped", () => {
  assert.equal(clean("  Auntie\n\tJo  ", 60), "Auntie Jo");
  assert.equal(clean("x".repeat(400), 300).length, 300);
  assert.equal(clean(null, 10), "");
});

test("chunk size suits Google's 256 KiB granularity and the Workers body limit", () => {
  assert.equal(LIMITS.chunk % 262144, 0);
  assert.ok(LIMITS.chunk < 100 * 1024 * 1024);
});

test("setup code hash matches wrangler.toml format", async () => {
  assert.match(await sha256("hunny"), /^[a-f0-9]{64}$/);
});

test("site storage pieces fit in one KV value, and caps are sane", () => {
  assert.ok(LIMITS.kvChunk <= 25 * 1024 * 1024);
  assert.ok(LIMITS.kv_video < LIMITS.video && LIMITS.kv_photo <= LIMITS.photo);
  assert.ok(LIMITS.kv_video >= 100 * 1024 * 1024);
});
