// 미니 블벅터 KR 서비스 워커 — 앱처럼 열리고, 오프라인이면 마지막으로 받은 화면을 보여 줌
//  · 화면(index)·manifest·아이콘: 네트워크 우선 → 실패하면 마지막으로 받은 것
//  · api/* (시세·수급·테마·뉴스 전부): 캐시하지 않음 — 항상 최신 또는 실패
//  · 다른 사이트(글꼴 등)는 건드리지 않음
const V = "mbbkr-v1";
const CACHEABLE = (u) => u.pathname === "/" || u.pathname.endsWith(".html") || /\.(png|webmanifest)$/.test(u.pathname);

self.addEventListener("install", (e) => {
  self.skipWaiting();
  e.waitUntil(caches.open(V).then((c) => c.addAll(["./", "manifest.webmanifest", "icon-192.png"])).catch(() => {}));
});

self.addEventListener("activate", (e) => {
  e.waitUntil(caches.keys().then((ks) => Promise.all(ks.filter((k) => k !== V).map((k) => caches.delete(k))))
    .then(() => self.clients.claim()));
});

self.addEventListener("fetch", (e) => {
  const r = e.request, u = new URL(r.url);
  if (r.method !== "GET" || u.origin !== location.origin || u.pathname.startsWith("/api/") || !CACHEABLE(u)) return;
  e.respondWith(
    fetch(r).then((res) => {
      if (res.ok) { const cp = res.clone(); caches.open(V).then((c) => c.put(r, cp)); }
      return res;
    }).catch(() => caches.match(r, { ignoreSearch: true })
      .then((m) => m || (r.mode === "navigate" ? caches.match("./") : null) || Response.error()))
  );
});
