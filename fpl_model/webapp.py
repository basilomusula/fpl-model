"""Turn the dashboard into an installable web app.

The dashboard on its own is a single HTML file, which is fine to open from
disk. To install it on a phone home screen it needs three more things served
from the same origin: a manifest, a service worker so it opens offline, and
real PNG icons. This module writes all of that into a folder ready to publish.

The icons are generated once and carried here as base64 so that publishing
never depends on an image library being installed.
"""

from __future__ import annotations

import base64
import os
import re

MANIFEST = """{
  "name": "FPL Squad Model",
  "short_name": "FPL Model",
  "description": "Projected points, best XI and transfer suggestions",
  "start_url": ".",
  "scope": ".",
  "display": "standalone",
  "orientation": "portrait-primary",
  "background_color": "#f6f6f4",
  "theme_color": "#1a3e6e",
  "icons": [
    {"src": "icon-192.png", "sizes": "192x192", "type": "image/png"},
    {"src": "icon-512.png", "sizes": "512x512", "type": "image/png"},
    {"src": "icon-maskable-512.png", "sizes": "512x512", "type": "image/png",
     "purpose": "maskable"}
  ]
}
"""

# Network-first with a cache fallback: always try for the freshest dashboard,
# but keep working on the Tube. The cache name carries the build stamp so a
# new publish supersedes the old copy instead of being shadowed by it.
SERVICE_WORKER = """const CACHE = "fpl-__STAMP__";
const ASSETS = ["./", "./index.html", "./manifest.webmanifest",
                "./icon-192.png", "./icon-512.png"];

self.addEventListener("install", e => {
  e.waitUntil(caches.open(CACHE).then(c => c.addAll(ASSETS))
    .then(() => self.skipWaiting()).catch(() => self.skipWaiting()));
});

self.addEventListener("activate", e => {
  e.waitUntil(caches.keys()
    .then(keys => Promise.all(keys.filter(k => k !== CACHE).map(k => caches.delete(k))))
    .then(() => self.clients.claim()));
});

self.addEventListener("fetch", e => {
  if (e.request.method !== "GET") return;
  e.respondWith(
    fetch(e.request).then(res => {
      const copy = res.clone();
      caches.open(CACHE).then(c => c.put(e.request, copy)).catch(() => {});
      return res;
    }).catch(() => caches.match(e.request).then(hit => hit || caches.match("./index.html")))
  );
});
"""

PWA_HEAD = """
<link rel="manifest" href="manifest.webmanifest">
<meta name="theme-color" content="#f6f6f4" media="(prefers-color-scheme: light)">
<meta name="theme-color" content="#1a1a19" media="(prefers-color-scheme: dark)">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-title" content="FPL Model">
<meta name="mobile-web-app-capable" content="yes">
<link rel="apple-touch-icon" href="icon-192.png">
<link rel="icon" type="image/png" sizes="512x512" href="icon-512.png">
"""

PWA_SCRIPT = """
<script>
if ("serviceWorker" in navigator) {
  addEventListener("load", () => navigator.serviceWorker
    .register("sw.js").catch(() => {}));
}
</script>
"""


def build_site(html: str, out_dir: str, stamp: str) -> list[str]:
    """Write an installable copy of the dashboard into out_dir.

    Returns the list of files written. `stamp` should change whenever the
    dashboard does - it is what tells an installed app to fetch the new one.
    """
    os.makedirs(out_dir, exist_ok=True)

    page = html.replace("</head>", PWA_HEAD + "</head>", 1)
    if PWA_HEAD not in page:                     # no </head> to anchor to
        page = PWA_HEAD + page
    page = page.replace("</body>", PWA_SCRIPT + "</body>", 1)

    written = []

    def write(name: str, data, binary: bool = False):
        path = os.path.join(out_dir, name)
        with open(path, "wb" if binary else "w",
                  **({} if binary else {"encoding": "utf-8"})) as fh:
            fh.write(data)
        written.append(name)

    write("index.html", page)
    write("manifest.webmanifest", MANIFEST)
    write("sw.js", SERVICE_WORKER.replace("__STAMP__", re.sub(r"[^0-9A-Za-z]", "", stamp)))
    write("icon-192.png", base64.b64decode(ICON_ICON_192), binary=True)
    write("icon-512.png", base64.b64decode(ICON_ICON_512), binary=True)
    write("icon-maskable-512.png", base64.b64decode(ICON_ICON_MASKABLE_512), binary=True)
    write(".nojekyll", "")   # GitHub Pages otherwise ignores dotfiles oddly
    return written


# ---------------------------------------------------------------------- #
# Icons, generated once and inlined so publishing needs no image library.
# ---------------------------------------------------------------------- #

ICON_ICON_192 = (
    "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"
    "RU5ErkJggg=="
)

ICON_ICON_512 = (
    "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"
    "+Dglh2g4AAAAAElFTkSuQmCC"
)

ICON_ICON_MASKABLE_512 = (
    "iVBORw0KGgoAAAANSUhEUgAAAgAAAAIACAYAAAD0eNT6AAALlElEQVR42u3dwZXaWBRFUYtFFEzI"
    "hDnJaEoMREQ8RIIHnrA8olbxpf/e3TuA7pKEuUff7vZyuqyvPwBAlINbAAACAAAQAACAAAAABAAA"
    "IAAAAAEAAAgAAEAAAAACAAAQAACAAAAABAAAIAAAAAEAAAgAAEAAAAACAAAEAAAgAAAAAQAACAAA"
    "QAAAAAIAABAAAIAAAAAEAAAgAAAAAQAACAAAQAAAAAIAABAAAIAAAAAEAAAIAABAAAAAAgAAEAAA"
    "gAAAAAQAACAAAAABAAAIAABAAAAAAgAAEAAAgAAAAAQAACAAAAABAAAIAAAQAACAAAAABAAAIAAA"
    "AAEAAAgAAEAAAAACAAAQAACAAAAABAAAIAAAAAEAAAgAAEAAAAACAAAQAAAgAAAAAQAACAAAQAAA"
    "AAIAABAAAIAAAAAEAAAgAAAAAQAACAAAQAAAAAIAABAAAIAAAAAEAAAgAABAALgFACAAAAABAAAI"
    "AABAAAAAAgAAEAAAgAAAAAQAACAAAAABAAAIAABAAAAAAgAAEAAAgAAAAAQAACAAAEAAAAACAAAQ"
    "AACAAAAABAAAIAAAAAEAAAgAAEAAAAACAAAQAACAAAAABAAAIAAAAAEAAAgAAEAAAIAAAAAEAAAg"
    "AAAAAQAACAAAQAAAAAIAABAAAIAAAAAEAAAgAAAAAQAACAAAQAAAAAIAABAAAIAAAAABAAAIAABA"
    "AAAAAgAAEAAAgAAAAAQAACAAAAABAAAIAABAAAAAAgAAEAAAgAAAAD52dAugn/P19tV/3vNxd1Oh"
    "meV0WV9uAxh5cQACADD2ogAEAGDsRQEIAMDoiwEQAIDRFwMgAACjLwZAAACGXwiAAACjjxgAAQCG"
    "HyEAAgAMP0IABAAYfoQACAAw/AgBEABg+BECIADA8CME4AcObgEYf88KnAAAxsRpADgBAOOPZwhO"
    "AMBo4DQABAAY//6D556AAADjb9DcMxAAYPiNl3sJAgCMv5Fyf0EAgHEySu43CAAwRkbI/QcBAIbH"
    "8HgeIAAgaGyMjOcDAgCCxsWweFYgACBoUIyJ5wYCAIJGxIB4hiAAIGg4jIbnCRX42wAxFsaihL3u"
    "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"
    "AEAAAAACAAAQAACAAAAABAAAIAAAAAEAAAgAAEAAAAACAAAQAAAgAAAAAQAACAAAQAAAAAIAABAA"
    "AIAAAAAEAAAgAAAAAQAACAAAQAAAAAIAABAAAIAAAAAEAAAgAABAAAAAAgAAEAAAgAAAAAQAACAA"
    "AAABAAAIAABAAAAAAgAAEAAAgAAAAAQAACAAAAABAAAIAABAAACAAAAABAAAIAAAAAEAAAgAAEAA"
    "AAACAAAQAACAAAAABAAAIAAAAAEAAAgAAEAAAAACAAAQAACAAAAAAeAWAIAAAAAEAAAgAAAAAQAA"
    "CAAAQAAAAAIAABAAAIAAAAAEAAAgAAAAAQAACAAAQAAAAAIAABAAAIAAAAABAAAIAABAAAAAAgAA"
    "EAAAgAAAAAQAACAAAAABAAAIAABAAAAAAgAAEAAAgAAAAAQAACAAAAABAAACAAAQAACAAAAABAAA"
    "IAAAAAEAAAgAAEAAAAACAAAQAACAAAAABAAAIAAAAAEAAAgAAEAAAAACAAAEAAAgAAAAAQAACAAA"
    "QAAAAAIAABAAAIAAAAAEAAAgAAAAAQAACAAAQAAAAAIAAPjYX5EWfCCaMEBYAAAAAElFTkSuQmCC"
)
