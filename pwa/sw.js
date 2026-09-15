// The service worker exists so the browser will treat this as an installable
// app rather than a bookmark. Chromium refuses to offer "Install" without one
// registered and holding a fetch handler; iOS does not need it, but honours
// the same manifest either way.
//
// It caches no DATA, deliberately. The library is a live view of a database on
// the server, and a cache-first worker would hand back last week's shelf off
// the phone's disk with no way to tell that is what happened. A stale answer
// about what you have read is worse than no answer, so every /api request goes
// to the network exactly as it would in a tab, and so does every cover.
//
// The document is a different thing, and the distinction is the whole design
// here. /ui.html is not an answer about the library, it is the program that
// asks the question: markup, stylesheet and script, the same bytes until the
// next deploy. Fetching it before anything can be drawn is why opening the
// installed app used to sit on white for as long as the network took, which
// is the difference a person feels between an app and a bookmark. So the
// document is served from the cache the instant it is asked for and refetched
// behind the render; when the copy that comes back differs from the one that
// was served, the page is told, and it offers a reload rather than taking one.
// The shelf that then paints is as live as it ever was — it comes from /api,
// which this worker still refuses to touch.
//
// The last thing it adds is the failure case. A navigation that cannot reach
// the server would otherwise render the browser's own error page — which,
// inside a standalone window with no address bar, is a dead end with nothing
// to press. So navigations get a small page of our own instead, with a retry.

const OFFLINE = `<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="color-scheme" content="dark"><title>Offline</title>
<style>
  html{background:#111;color:#e8e8e8;font:16px/1.5 system-ui,sans-serif}
  body{display:grid;place-content:center;gap:20px;min-height:100vh;margin:0;
       padding:24px;text-align:center}
  h1{font-size:19px;font-weight:600;margin:0}
  p{margin:0;color:#8f8f8f;max-width:30ch}
  button{background:#3dd68c;color:#0b0b0b;border:0;border-radius:8px;
         padding:11px 22px;font:inherit;font-weight:600}
</style></head><body>
<h1>Can't reach the tracker</h1>
<p>The server is unreachable. Your data is on it, not here.</p>
<button onclick="location.reload()">Try again</button>
</body></html>`;

// Bumping this name is how a cached document is abandoned rather than
// migrated: the old cache is deleted on activate and the next open refills it.
const SHELL = "shell-v1";
const DOC = "/";

self.addEventListener("install", () => self.skipWaiting());
self.addEventListener("activate", (e) => e.waitUntil((async () => {
  for (const k of await caches.keys()) if (k !== SHELL) await caches.delete(k);
  await self.clients.claim();
})()));

self.addEventListener("fetch", (e) => {
  if (e.request.mode !== "navigate") return;   // everything else: straight through
  // Only the app's own document is a shell. /login is a different page with a
  // different job, and caching it would mean opening the app to a login form
  // that had already been satisfied.
  if (new URL(e.request.url).pathname !== DOC) {
    e.respondWith(fetch(e.request).catch(() => offline()));
    return;
  }
  e.respondWith(appDocument(e));
});

const offline = () => new Response(OFFLINE, {
  status: 503,
  headers: { "Content-Type": "text/html; charset=utf-8" },
});

/* The revalidation of the document this worker is currently serving, and what
   it concluded. The page asks for the verdict rather than the worker pushing
   it: at the moment the refetch finishes — milliseconds after the navigation —
   the document it belongs to is still loading and is not a window client yet,
   so a `clients.matchAll()` then posts into an empty room. The page, by
   contrast, knows exactly when it exists. */
let checking = null;
let stale = false;

async function appDocument(e) {
  const cache = await caches.open(SHELL);
  const held = await cache.match(DOC);
  // Read the held copy NOW, while it is still ours. Once it is handed back as
  // the response its body belongs to the browser, and a clone taken after that
  // is a clone of a stream already being drained.
  const heldText = held ? await held.clone().text() : null;

  stale = false;
  // A redirect means the session is gone and the server is sending us to the
  // login page: that response is not the app and must never become the shell.
  checking = fetch(e.request).then(async (res) => {
    if (res.ok && !res.redirected &&
        (res.headers.get("Content-Type") || "").startsWith("text/html")) {
      const copy = res.clone();
      const next = await copy.clone().text();
      await cache.put(DOC, copy);
      stale = heldText !== null && next !== heldText;
    }
    return res;
  });

  if (held) {
    // The render starts now, off the disk. The refetch above keeps running on
    // the worker's own time — `waitUntil` is what stops the browser killing it
    // the moment this response is handed over.
    e.waitUntil(checking.catch(() => {}));
    return held;
  }
  // Nothing held: first open after install, or after a cache wipe.
  return checking.catch(() => offline());
}

// "Is what I am running still what is on the server?" — asked by the page once
// it has finished loading. Waiting on `checking` first is the point: the answer
// is not ready until the refetch is, and a page that asked early would be told
// everything was fine and never ask again.
self.addEventListener("message", (e) => {
  if (!e.data || e.data.type !== "shell?") return;
  e.waitUntil((async () => {
    try { await checking; } catch { /* offline: nothing to report */ }
    e.source && e.source.postMessage({ type: stale ? "shell-stale" : "shell-fresh" });
  })());
});
