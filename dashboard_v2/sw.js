// Phase 17.5 — minimal service worker for PWA install + offline shell.
// Cache strategy: shell (HTML, manifest, icons) cache-first; everything
// else network-first so live API/WS data stays fresh.

const SHELL = "nexus-shell-v1";
const SHELL_URLS = ["/", "/manifest.json", "/icon-192.svg", "/icon-512.svg"];

self.addEventListener("install", (e) => {
  e.waitUntil(caches.open(SHELL).then((c) => c.addAll(SHELL_URLS)));
  self.skipWaiting();
});

self.addEventListener("activate", (e) => {
  e.waitUntil(
    caches.keys().then((keys) =>
      Promise.all(keys.filter((k) => k !== SHELL).map((k) => caches.delete(k)))
    )
  );
  self.clients.claim();
});

self.addEventListener("fetch", (e) => {
  const url = new URL(e.request.url);
  // Never cache API or WS — they need live data.
  if (url.port === "11435" || url.pathname.startsWith("/api/") || url.pathname.startsWith("/ws/")) {
    return;
  }
  if (SHELL_URLS.includes(url.pathname)) {
    e.respondWith(
      caches.match(e.request).then((hit) => hit || fetch(e.request).then((res) => {
        const copy = res.clone();
        caches.open(SHELL).then((c) => c.put(e.request, copy));
        return res;
      }).catch(() => caches.match("/")))
    );
  }
});
