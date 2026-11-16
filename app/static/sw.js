// Minimal service worker: makes the site installable (needed for the
// "Share -> SocialFetch" target on Android). Network only, no caching.
self.addEventListener("install", () => self.skipWaiting());
self.addEventListener("activate", (e) => e.waitUntil(self.clients.claim()));
self.addEventListener("fetch", () => {});
