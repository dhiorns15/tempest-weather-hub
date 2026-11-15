// Service worker for the installable app (registered by js/pwa.js, only in
// a secure context). It lives at the site root so its scope is the whole
// site.
//
// Network first, always: an online visitor gets exactly what the server has
// now, never an older cached copy, so an update shows up on the next load.
// The cache is only a fallback for when the network can't answer.
//
// Live data is never cached or answered from cache: offline, those requests
// simply fail and the page says it couldn't load readings, instead of
// showing old readings as if they were current. A page load that fails gets
// the offline page, which says so plainly.

const CACHE = "weatherhub-v1";
const OFFLINE_URL = "/offline.html";
const PRECACHE = [OFFLINE_URL, "/css/site.css", "/js/theme.js", "/favicon.svg"];
const LIVE_DATA = /^\/(api|site|widget|kiosk|healthz)(\/|$)/;

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches
      .open(CACHE)
      .then((cache) => cache.addAll(PRECACHE))
      .then(() => self.skipWaiting())
  );
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches
      .keys()
      .then((keys) =>
        Promise.all(
          keys
            .filter((key) => key.startsWith("weatherhub-") && key !== CACHE)
            .map((key) => caches.delete(key))
        )
      )
      .then(() => self.clients.claim())
  );
});

async function networkFirst(request) {
  try {
    const response = await fetch(request);
    if (response.status === 200 && response.type === "basic") {
      const copy = response.clone();
      caches.open(CACHE).then((cache) => cache.put(request, copy));
    }
    return response;
  } catch (err) {
    const cached = await caches.match(request);
    if (cached) return cached;
    throw err;
  }
}

async function pageOrOffline(request) {
  try {
    return await fetch(request);
  } catch (err) {
    return (await caches.match(OFFLINE_URL)) || Response.error();
  }
}

self.addEventListener("fetch", (event) => {
  const { request } = event;
  if (request.method !== "GET") return;

  const url = new URL(request.url);
  if (url.origin !== self.location.origin) return;
  if (LIVE_DATA.test(url.pathname)) return; // straight to the network, never cached

  if (request.mode === "navigate") {
    event.respondWith(pageOrOffline(request));
    return;
  }
  event.respondWith(networkFirst(request));
});
