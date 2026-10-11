// Cache the public shell only. Authenticated carts, tokens, receipts and evidence are never cached.
const CACHE = "shelfflow-shell-v2";
self.addEventListener("install", (e) =>
  e.waitUntil(
    caches
      .open(CACHE)
      .then((c) =>
        c.addAll([
          "/",
          "/icon.svg",
          "/icon-192.png",
          "/icon-512.png",
          "/manifest.webmanifest",
        ]),
      ),
  ),
);
self.addEventListener("activate", (e) =>
  e.waitUntil(
    caches
      .keys()
      .then((keys) =>
        Promise.all(
          keys.filter((k) => k !== CACHE).map((k) => caches.delete(k)),
        ),
      ),
  ),
);
self.addEventListener("fetch", (e) => {
  const u = new URL(e.request.url);
  if (
    e.request.method === "GET" &&
    u.origin === location.origin &&
    !u.pathname.startsWith("/api") &&
    !u.pathname.startsWith("/ws")
  )
    e.respondWith(
      fetch(e.request)
        .then((response) => {
          if (
            response.ok &&
            (u.pathname.startsWith("/assets/") || u.pathname === "/")
          ) {
            const copy = response.clone();
            caches.open(CACHE).then((c) => c.put(e.request, copy));
          }
          return response;
        })
        .catch(() =>
          caches.match(e.request).then(
            (r) =>
              r ||
              new Response("Offline. Reconnect to load your live cart.", {
                status: 503,
              }),
          ),
        ),
    );
});
