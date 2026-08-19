"""App scaffolding — generate a new app skeleton with `kirocrew app init`.

Creates a minimal app directory structure with a valid manifest,
sample agent, sample skill, and optional backend/UI stubs.
"""
from __future__ import annotations

import json
import logging
import struct
import zlib
from pathlib import Path

logger = logging.getLogger(__name__)

#: Geometry and palette of the scaffolded placeholder icon: a plate inset in a
#: darker field, at the size the publishing guide asks for and neutral enough to
#: read on both a light and a dark card.
_ICON_PX = 512
_ICON_INSET = 128
_ICON_FIELD = (46, 52, 64)
_ICON_PLATE = (67, 76, 94)


def _placeholder_icon_png() -> bytes:
    """Encode the placeholder store icon, standard library only.

    Pillow is not a dependency of this path, and taking one on so that
    ``app init`` can draw a rectangle would be a poor trade: a PNG is a signature
    followed by length-tag-payload-CRC chunks, so emitting one directly is
    shorter than the argument for the dependency would be.

    Truecolor (colour type 2), not RGBA. The publishing guide requires an opaque
    icon -- an opaque tile carries its own background, which is what makes the
    dark variant optional rather than a latent bug -- so carrying an alpha
    channel would model a degree of freedom the icon is not allowed to use.

    The bytes are identical for every app, which is deliberate: a single known
    digest stays recognisable as "still the placeholder", which a per-app colour
    would trade away for nothing.
    """
    field = bytes(_ICON_FIELD) * _ICON_PX
    margin = bytes(_ICON_FIELD) * _ICON_INSET
    plate = bytes(_ICON_PLATE) * (_ICON_PX - 2 * _ICON_INSET)
    raw = bytearray()
    for y in range(_ICON_PX):
        # Leading byte is the scanline filter type: 0, meaning the row is stored
        # as-is. Every row is a run of at most two colours, which deflate folds
        # down to well under a kilobyte.
        inside = _ICON_INSET <= y < _ICON_PX - _ICON_INSET
        raw += b"\x00" + (margin + plate + margin if inside else field)

    def chunk(tag: bytes, payload: bytes) -> bytes:
        return (
            struct.pack(">I", len(payload))
            + tag
            + payload
            + struct.pack(">I", zlib.crc32(tag + payload) & 0xFFFFFFFF)
        )

    # width, height, bit depth, colour type, compression, filter, interlace
    ihdr = struct.pack(">IIBBBBB", _ICON_PX, _ICON_PX, 8, 2, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", ihdr)
        + chunk(b"IDAT", zlib.compress(bytes(raw), 9))
        + chunk(b"IEND", b"")
    )


_MANIFEST_TEMPLATE = {
    "name": "",
    "version": "0.1.0",
    "displayName": "",
    "description": "",
    "author": "",
    # The store's card and row icon, repo-relative. Scaffolded rather than left
    # to the publishing guide: a field nobody knows exists is a field nobody
    # fills, and an entry that reaches the catalog without one renders as a
    # generated placeholder that looks like a store bug rather than an
    # incomplete manifest.
    "iconPath": "assets/icon.png",
    "agents": ["agents/sample-agent.json"],
    "skills": ["skills/sample-skill"],
    "tags": [],
}

_AGENT_TEMPLATE = {
    "name": "sample-agent",
    "model": "auto",
    "description": "A sample agent — customize this for your use case",
    "prompt": "You are a helpful assistant.",
    "tools": [],
}

_SKILL_TEMPLATE = """---
description: Sample skill for {display_name}
always: false
---

# {display_name} — Sample Skill

This skill provides domain knowledge for the {name} app.

## What This Skill Does

Describe what this skill teaches the agent.

## Key Concepts

- Concept 1
- Concept 2

## Common Patterns

Describe common patterns the agent should know about.
"""

_BACKEND_TEMPLATE = '''"""Minimal backend for {name} — a KiroCrew app.

Run with: python backend/server.py
Or let KiroCrew manage it via the app manifest backend section.
"""
import json
import os

from http.server import BaseHTTPRequestHandler, HTTPServer

PORT = int(os.environ.get("PORT", 9100))
APP_NAME = os.environ.get("KIROCREW_APP_NAME", "{name}")


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/health":
            self._json(200, {{"status": "ok", "app": APP_NAME}})
        elif self.path == "/api/apps/{name}/status":
            self._json(200, {{"app": APP_NAME, "version": "0.1.0"}})
        else:
            self._json(404, {{"error": "not found"}})

    def _json(self, code, data):
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(data).encode())

    def log_message(self, *args):
        pass


if __name__ == "__main__":
    print(f"{{APP_NAME}} backend on port {{PORT}}")
    HTTPServer(("127.0.0.1", PORT), Handler).serve_forever()
'''

_UI_PACKAGE_JSON_TEMPLATE = """\
{{
  "name": "{name}-ui",
  "private": true,
  "type": "module",
  "scripts": {{
    "dev": "vite",
    "build": "vite build"
  }},
  "dependencies": {{
    "react": "^18.2.0",
    "react-dom": "^18.2.0"
  }},
  "devDependencies": {{
    "@vitejs/plugin-react": "^4.2.0",
    "vite": "^5.0.0"
  }},
  "peerDependencies": {{
    "@kirocrew/app-sdk": "*",
    "lucide-react": "*"
  }}
}}
"""

_UI_VITE_CONFIG_TEMPLATE = """\
import {{ defineConfig }} from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({{
  plugins: [react()],
  build: {{
    lib: {{
      entry: 'src/App.tsx',
      formats: ['es'],
      fileName: () => 'index.mjs',
    }},
    outDir: 'dist',
    rollupOptions: {{
      external: [
        'react', 'react-dom', 'react/jsx-runtime',
        '@kirocrew/app-sdk', '@kirocrew/app-sdk/ui', 'lucide-react',
      ],
    }},
  }},
}})
"""

_UI_APP_TSX_TEMPLATE = """\
import {{ useAppApi }} from '@kirocrew/app-sdk'
import {{ Card, CardTitle, PageHeader, StatCard }} from '@kirocrew/app-sdk/ui'
import {{ useState, useEffect }} from 'react'

export default function {component_name}() {{
  const api = useAppApi()
  const [loading, setLoading] = useState(true)

  useEffect(() => {{
    // Fetch initial data here
    setLoading(false)
  }}, [])

  return (
    <>
      <PageHeader title="{display_name}" subtitle="{description}" />
      <div className="px-6 pb-8 overflow-y-auto flex-1 min-h-0">
        <div className="grid gap-3.5 grid-cols-[repeat(auto-fit,minmax(150px,1fr))] mb-6">
          <StatCard label="Status" value="OK" accent />
        </div>
        <Card>
          <CardTitle>Overview</CardTitle>
          {{loading
            ? <p className="text-sm text-muted">Loading…</p>
            : <p className="text-sm text-muted">Your app content goes here.</p>
          }}
        </Card>
      </div>
    </>
  )
}}
"""

_UI_GITIGNORE_TEMPLATE = """\
node_modules/
"""

_README_TEMPLATE = """# {display_name}

{description}

## Installation

```bash
kirocrew app install /path/to/{name}
kirocrew app enable {name}
```

## Development

Edit agents, skills, and backend code. Changes to agents and skills
take effect on next agent invocation. Backend changes require restart.

## Structure

```
{name}/
├── app.json              ← manifest
├── assets/
│   └── icon.png          ← store icon; replace this placeholder
├── agents/               ← agent definitions
│   └── sample-agent.json
├── skills/               ← skill files
│   └── sample-skill/
│       └── SKILL.md
├── backend/              ← optional backend
│   └── server.py
└── README.md
```
"""


def scaffold_app(
    output_dir: Path,
    name: str,
    *,
    display_name: str = "",
    description: str = "",
    author: str = "",
    include_backend: bool = False,
    include_ui: bool = False,
    include_cron: bool = False,
) -> Path:
    """Create a new app skeleton at *output_dir*.

    Returns the path to the created app directory.
    """
    if not display_name:
        display_name = name.replace("-", " ").title()
    if not description:
        description = f"A Kiro Crew app: {display_name}"
    if not author:
        import os
        author = os.environ.get("USER", "developer")

    app_dir = output_dir / name
    app_dir.mkdir(parents=True, exist_ok=True)

    # Manifest
    manifest: dict[str, object] = {**_MANIFEST_TEMPLATE}
    manifest["name"] = name
    manifest["displayName"] = display_name
    manifest["description"] = description
    manifest["author"] = author
    if include_backend:
        manifest["backend"] = {
            "entryPoint": "backend/server.py",
            "port": "auto",
            "healthCheck": "/health",
        }
    if include_ui:
        manifest["ui"] = {
            "entry": "dist/index.mjs",
            "pages": [
                {
                    "route": f"/apps/{name}",
                    "label": display_name,
                    "icon": "Package",
                }
            ],
        }
    if include_cron:
        manifest["crons"] = [
            {
                "name": f"{name}-check",
                "every": 300,
                "message": f"Run periodic check for {display_name}",
            }
        ]
    (app_dir / "app.json").write_text(
        json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
    )

    # Store icon. Real bytes, not just the manifest key: an `iconPath` naming a
    # file that does not exist publishes worse than naming nothing, because the
    # store's fallback is identical either way and the developer gets a broken
    # reference instead of a working default. Shipping a valid opaque square
    # makes an iconless app a state someone has to CREATE by deleting this, not
    # one they fall into by never reading the publishing guide.
    #
    # Written only when absent, unlike every other file here. The rest are
    # GENERATED -- re-running `app init` reproduces them from the same arguments,
    # so overwriting costs nothing. This one is the developer's ARTWORK the moment
    # they replace it, which is the entire point of scaffolding it, so an
    # unconditional write would make a second `app init` destroy the icon.
    assets_dir = app_dir / "assets"
    assets_dir.mkdir(exist_ok=True)
    # Resolve before BOTH the existence test and the write. `exists()` is False for a
    # DANGLING symlink, so testing the joined path would fall through to a write that
    # follows the link and lands outside the app; a symlinked `assets` escapes the
    # same way. Both sides are resolved so a symlink in the user's own `--dir` (a
    # symlinked home, /tmp on macOS) compares equal instead of reading as an escape.
    icon = (assets_dir / "icon.png").resolve()
    if not icon.is_relative_to(app_dir.resolve()):
        logger.warning("refusing to write an icon outside %s: %s", app_dir, icon)
    elif not icon.exists():
        icon.write_bytes(_placeholder_icon_png())

    # Agent
    agents_dir = app_dir / "agents"
    agents_dir.mkdir(exist_ok=True)
    (agents_dir / "sample-agent.json").write_text(
        json.dumps(_AGENT_TEMPLATE, indent=2) + "\n", encoding="utf-8"
    )

    # Skill
    skill_dir = app_dir / "skills" / "sample-skill"
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text(
        _SKILL_TEMPLATE.format(name=name, display_name=display_name),
        encoding="utf-8",
    )

    # Backend (optional)
    if include_backend:
        backend_dir = app_dir / "backend"
        backend_dir.mkdir(exist_ok=True)
        (backend_dir / "server.py").write_text(
            _BACKEND_TEMPLATE.format(name=name), encoding="utf-8"
        )

    # UI (optional)
    if include_ui:
        ui_dir = app_dir / "ui"
        ui_dir.mkdir(exist_ok=True)
        src_dir = ui_dir / "src"
        src_dir.mkdir(exist_ok=True)

        component_name = name.replace("-", " ").title().replace(" ", "")

        (ui_dir / "package.json").write_text(
            _UI_PACKAGE_JSON_TEMPLATE.format(name=name), encoding="utf-8"
        )
        (ui_dir / "vite.config.ts").write_text(
            _UI_VITE_CONFIG_TEMPLATE.format(), encoding="utf-8"
        )
        (src_dir / "App.tsx").write_text(
            _UI_APP_TSX_TEMPLATE.format(
                component_name=component_name,
                display_name=display_name,
                description=description,
            ),
            encoding="utf-8",
        )
        (ui_dir / ".gitignore").write_text(
            _UI_GITIGNORE_TEMPLATE, encoding="utf-8"
        )

    # README
    (app_dir / "README.md").write_text(
        _README_TEMPLATE.format(
            name=name, display_name=display_name, description=description
        ),
        encoding="utf-8",
    )

    logger.info("Scaffolded app %s at %s", name, app_dir)
    return app_dir
