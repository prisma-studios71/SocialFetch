"use strict";

const $ = (sel) => document.querySelector(sel);
const PLATFORM_NAMES = { instagram: "Instagram", tiktok: "TikTok", x: "X" };

let current = null; // last resolved post

async function api(path, body) {
  const res = await fetch(path, {
    method: body ? "POST" : "GET",
    headers: body ? { "Content-Type": "application/json" } : {},
    body: body ? JSON.stringify(body) : undefined,
    credentials: "same-origin",
  });
  let data = {};
  try { data = await res.json(); } catch { /* empty body */ }
  if (res.status === 401 && path !== "/api/login") showLogin();
  if (!res.ok) throw new Error(data.detail || data.error || "Coś poszło nie tak.");
  return data;
}

// ---------------------------------------------------------------- login ---

function showLogin() {
  $("#app").hidden = true;
  $("#login").hidden = false;
  $("#password").focus();
}

function showApp() {
  $("#login").hidden = true;
  $("#app").hidden = false;
}

$("#login-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const btn = e.submitter;
  btn.setAttribute("aria-busy", "true");
  $("#login-error").hidden = true;
  try {
    await api("/api/login", { password: $("#password").value });
    showApp();
    handleShared();
  } catch (err) {
    $("#login-error").textContent = err.message;
    $("#login-error").hidden = false;
  } finally {
    btn.removeAttribute("aria-busy");
  }
});

// --------------------------------------------------------------- search ---

function setError(msg) {
  $("#error").textContent = msg || "";
  $("#error").hidden = !msg;
}

async function lookup(url) {
  setError("");
  $("#result").replaceChildren();
  $("#loading").hidden = false;
  $("#go").setAttribute("aria-busy", "true");
  try {
    current = await api("/api/info", { url });
    render(current);
  } catch (err) {
    setError(err.message);
  } finally {
    $("#loading").hidden = true;
    $("#go").removeAttribute("aria-busy");
  }
}

$("#search").addEventListener("submit", (e) => {
  e.preventDefault();
  const url = $("#url").value.trim();
  if (url) lookup(url);
});

// Pasting a link searches right away.
$("#url").addEventListener("paste", () => {
  setTimeout(() => {
    const v = $("#url").value.trim();
    if (/^https?:\/\//i.test(v)) lookup(v);
  }, 0);
});

$("#paste").addEventListener("click", async () => {
  try {
    const text = (await navigator.clipboard.readText()).trim();
    if (!text) return;
    $("#url").value = text;
    lookup(text);
  } catch {
    $("#url").focus();
    setError("Przeglądarka nie dała dostępu do schowka. Wklej link ręcznie.");
  }
});

// ------------------------------------------------------------ rendering ---

function fmtDuration(sec) {
  if (!sec) return "";
  const s = Math.round(sec);
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
}

function dlUrl(params) {
  const q = new URLSearchParams({ u: current.url, ...params });
  return `/api/download?${q}`;
}

function thumbUrl(i) {
  return `/api/thumb?${new URLSearchParams({ u: current.url, i })}`;
}

// Downloads are plain navigations (lets the browser stream big files to disk).
// The server echoes a token back as a cookie, which is how we know when to stop the spinner.
function wireDownload(link, params) {
  link.href = dlUrl(params);
  link.addEventListener("click", (e) => {
    if (link.getAttribute("aria-busy") === "true") { e.preventDefault(); return; }
    const token = Math.random().toString(36).slice(2, 12);
    link.href = dlUrl({ ...params, t: token });
    link.setAttribute("aria-busy", "true");
    const started = Date.now();
    const timer = setInterval(() => {
      const done = document.cookie.split("; ").includes(`sf_dl=${token}`);
      if (done || Date.now() - started > 180000) {
        clearInterval(timer);
        link.removeAttribute("aria-busy");
      }
    }, 400);
  });
}

function render(post) {
  const root = $("#result");
  const card = document.createElement("div");
  card.className = "card";

  const head = document.createElement("div");
  head.className = "post-head";
  const pill = document.createElement("span");
  pill.className = "pill";
  pill.textContent = PLATFORM_NAMES[post.platform] || post.platform;
  head.append(pill);
  if (post.author) {
    const a = document.createElement("span");
    a.className = "author";
    a.textContent = "@" + post.author.replace(/^@/, "");
    head.append(a);
  }
  card.append(head);

  if (post.caption) {
    const c = document.createElement("p");
    c.className = "caption";
    c.textContent = post.caption;
    card.append(c);
  }

  const multi = post.items.length > 1;
  if (multi || (post.has_audio && post.items.every((i) => i.kind !== "video"))) {
    const bulk = document.createElement("div");
    bulk.className = "bulk";
    if (multi) {
      const z = document.createElement("a");
      z.className = "btn primary";
      z.rel = "nofollow";
      z.textContent = `📦 Wszystko (${post.items.length}) .zip`;
      wireDownload(z, { mode: "zip" });
      bulk.append(z);
    }
    if (post.has_audio && post.items.every((i) => i.kind !== "video")) {
      const m = document.createElement("a");
      m.className = "btn ghost";
      m.rel = "nofollow";
      m.textContent = "🎵 Dźwięk MP3";
      wireDownload(m, { mode: "audio" });
      bulk.append(m);
    }
    card.append(bulk);
  }

  const list = document.createElement("div");
  list.className = "items" + (multi ? " grid" : "");
  post.items.forEach((item, i) => list.append(renderItem(item, i, multi)));
  card.append(list);

  root.replaceChildren(card);
}

function renderItem(item, i, multi) {
  const node = $("#item-tpl").content.firstElementChild.cloneNode(true);
  const img = node.querySelector("img");
  if (item.thumb) img.src = thumbUrl(i);
  else img.remove();
  img.alt = item.kind === "video" ? "Podgląd filmu" : "Zdjęcie";
  img.addEventListener("error", () => img.remove());

  const badge = node.querySelector(".badge");
  const label = item.kind === "video" ? `▶ ${fmtDuration(item.duration)}`.trim() : "🖼";
  badge.textContent = multi ? `${i + 1} · ${label}` : label;

  const select = node.querySelector(".quality");
  const mp3 = node.querySelector(".mp3");

  if (item.qualities.length > 1) {
    for (const q of item.qualities) select.add(new Option(q.label, q.id));
  } else {
    select.remove();
  }

  const update = () => {
    const q = select.isConnected ? select.value : item.qualities[0]?.id;
    const old = node.querySelector(".dl");
    const fresh = old.cloneNode(true); // drops the old click listener
    old.replaceWith(fresh);
    fresh.textContent = item.kind === "video" ? "⬇ Wideo" : "⬇ Zdjęcie";
    wireDownload(fresh, { mode: "item", i, q });
  };
  select.addEventListener("change", update);
  update();

  if (item.kind === "video") wireDownload(mp3, { mode: "audio", i });
  else mp3.remove();

  return node;
}

// -------------------------------------------------- share target / boot ---

function handleShared() {
  const params = new URLSearchParams(location.search);
  const shared = params.get("url") || params.get("text") || "";
  if (!shared) return;
  history.replaceState(null, "", "/");
  $("#url").value = shared;
  lookup(shared);
}

(async function boot() {
  if ("serviceWorker" in navigator) navigator.serviceWorker.register("/sw.js").catch(() => {});
  try {
    const me = await api("/api/me");
    if (me.authed) {
      showApp();
      handleShared();
    } else {
      showLogin();
    }
  } catch {
    showApp();
    setError("Serwer nie odpowiada. Jeśli strona dopiero się budzi, odczekaj chwilę i odśwież.");
  }
})();
