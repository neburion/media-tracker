// The service worker exists so the browser will treat this as an installable
// app rather than a bookmark. Chromium refuses to offer "Install" without one
// registered and holding a fetch handler; iOS does not need it, but honours
// the same manifest either way.
//
// It caches nothing, deliberately. This page is a live view of a database on
// the server, and a cache-first worker would hand back last week's library off
// the phone's disk with no way to tell that is what happened. A stale answer
// about what you have read is worse than no answer, so every request goes to
// the network exactly as it would in a tab.
//
// The one thing it adds is the failure case. A navigation that cannot reach
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

self.addEventListener("install", () => self.skipWaiting());
self.addEventListener("activate", (e) => e.waitUntil(self.clients.claim()));

self.addEventListener("fetch", (e) => {
  if (e.request.mode !== "navigate") return;   // everything else: straight through
  e.respondWith(
    fetch(e.request).catch(() => new Response(OFFLINE, {
      status: 503,
      headers: { "Content-Type": "text/html; charset=utf-8" },
    })),
  );
});
