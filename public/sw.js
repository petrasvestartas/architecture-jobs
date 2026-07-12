/* Architecture Jobs Switzerland — service worker
   Strategy: network-first for every request, falling back to cache when offline. */
"use strict";
var CACHE = "ajs-v1";
var PRECACHE = ["./", "index.html", "data.js", "icon.svg", "manifest.webmanifest"];

self.addEventListener("install", function (e) {
  e.waitUntil(
    caches.open(CACHE).then(function (c) {
      /* precache one by one so a single failure doesn't abort install */
      return Promise.all(PRECACHE.map(function (u) {
        return c.add(u).catch(function () {});
      }));
    }).then(function () { return self.skipWaiting(); })
  );
});

self.addEventListener("activate", function (e) {
  e.waitUntil(
    caches.keys().then(function (keys) {
      return Promise.all(keys.filter(function (k) { return k !== CACHE; })
        .map(function (k) { return caches.delete(k); }));
    }).then(function () { return self.clients.claim(); })
  );
});

self.addEventListener("fetch", function (e) {
  if (e.request.method !== "GET") return;
  e.respondWith(
    fetch(e.request).then(function (res) {
      if (res && (res.ok || res.type === "opaque")) {
        var clone = res.clone();
        caches.open(CACHE).then(function (c) { c.put(e.request, clone); }).catch(function () {});
      }
      return res;
    }).catch(function () {
      return caches.match(e.request).then(function (m) {
        if (m) return m;
        if (e.request.mode === "navigate") return caches.match("index.html");
        return Response.error();
      });
    })
  );
});
