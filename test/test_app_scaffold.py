"""Tests for kiro_crew.apps.scaffold — app scaffolding."""
from __future__ import annotations

import json
import struct
import zlib

from kiro_crew.apps.manifest import AppManifest
from kiro_crew.apps.scaffold import _placeholder_icon_png, scaffold_app


class TestPlaceholderIcon:
    """The scaffolded store icon, pinned against the publishing guide's spec.

    A scaffolded app that reaches the App Store catalog with no icon publishes a
    generated placeholder card, indistinguishable from an icon the publish
    pipeline dropped -- so it reads as a store bug rather than an incomplete
    manifest. These pin the shape the guide actually requires, so a change here
    cannot silently produce an icon the store rejects.
    """

    def test_is_a_png(self):
        assert _placeholder_icon_png()[:8] == b"\x89PNG\r\n\x1a\n"

    def test_is_the_square_512_the_guide_asks_for(self):
        width, height = struct.unpack(">II", _placeholder_icon_png()[16:24])
        assert width == height == 512

    def test_carries_no_alpha_channel(self):
        """Colour type 2 is truecolor RGB. The guide requires an opaque icon, so
        an alpha channel would model a freedom the icon cannot use."""
        assert _placeholder_icon_png()[25] == 2

    def test_stays_small(self):
        """Two flat colours should compress to nothing; a regression that
        inflates this would otherwise be silent."""
        assert len(_placeholder_icon_png()) < 8 * 1024

    def test_is_byte_identical_across_calls(self):
        """One known digest stays recognisable as 'still the placeholder'."""
        assert _placeholder_icon_png() == _placeholder_icon_png()

    def test_pixels_decode_to_the_intended_plate(self):
        """Cheap proof the scanline filter byte and row stride are right: a wrong
        stride still produces a file every header check above accepts."""
        data = _placeholder_icon_png()
        raw = zlib.decompress(data[data.index(b"IDAT") + 4 : -12])
        stride = 1 + 512 * 3
        assert len(raw) == 512 * stride
        middle = raw[256 * stride : 257 * stride]
        assert middle[0] == 0, "scanline filter type must be None"
        assert tuple(middle[1:4]) == (46, 52, 64), "row starts in the field"
        centre = 1 + 256 * 3
        assert tuple(middle[centre : centre + 3]) == (67, 76, 94), "plate inside"


class TestScaffold:
    def test_basic_scaffold(self, tmp_path):
        app_dir = scaffold_app(tmp_path, "my-test-app")
        assert app_dir.is_dir()
        assert (app_dir / "app.json").is_file()
        assert (app_dir / "agents" / "sample-agent.json").is_file()
        assert (app_dir / "skills" / "sample-skill" / "SKILL.md").is_file()
        assert (app_dir / "README.md").is_file()

        # Manifest should be valid
        m = AppManifest.from_json_file(app_dir / "app.json")
        assert m.name == "my-test-app"
        assert m.validate() == []

    def test_store_icon_exists_and_is_declared(self, tmp_path):
        """Both halves together. The bytes without the manifest key are an unused
        file; the key without the bytes is a broken reference, which publishes
        worse than declaring nothing at all."""
        app_dir = scaffold_app(tmp_path, "icon-app")
        icon = app_dir / "assets" / "icon.png"
        assert icon.is_file()
        assert icon.read_bytes() == _placeholder_icon_png()
        manifest = json.loads((app_dir / "app.json").read_text(encoding="utf-8"))
        assert manifest["iconPath"] == "assets/icon.png"

    def test_icon_path_resolves_from_the_app_root(self, tmp_path):
        """`iconPath` is repo-relative, so it must resolve against the app
        directory exactly as written -- no leading slash, no `ui/` prefix."""
        app_dir = scaffold_app(tmp_path, "resolve-app")
        declared = json.loads((app_dir / "app.json").read_text(encoding="utf-8"))
        assert (app_dir / declared["iconPath"]).is_file()

    def test_rerun_does_not_destroy_a_replaced_icon(self, tmp_path):
        """Every other scaffolded file is GENERATED and reproduced from the same
        arguments, so overwriting costs nothing. This one becomes the developer's
        artwork the moment they replace it -- which is the point of scaffolding it
        -- so a second `app init` must not overwrite their icon."""
        app_dir = scaffold_app(tmp_path, "rerun-app")
        icon = app_dir / "assets" / "icon.png"
        icon.write_bytes(b"\x89PNG\r\n\x1a\nnot-the-placeholder")

        scaffold_app(tmp_path, "rerun-app")
        assert icon.read_bytes() == b"\x89PNG\r\n\x1a\nnot-the-placeholder"

    def test_icon_write_refuses_to_follow_an_escaping_symlink(self, tmp_path):
        """`exists()` is False for a DANGLING symlink, so an unresolved existence
        test would fall through to a write that follows the link out of the app
        directory. Containment is checked on the resolved path."""
        outside = tmp_path / "outside"
        outside.mkdir()
        target = outside / "pwned.png"

        app_dir = tmp_path / "out" / "link-app"
        (app_dir / "assets").mkdir(parents=True)
        (app_dir / "assets" / "icon.png").symlink_to(target)

        scaffold_app(tmp_path / "out", "link-app")
        assert not target.exists(), "write escaped the app directory"

    def test_icon_write_refuses_an_escaping_assets_symlink(self, tmp_path):
        """The same escape one level up: `assets` itself is the symlink, so the
        joined path looks contained while the resolved one is not."""
        outside = tmp_path / "outside"
        outside.mkdir()

        app_dir = tmp_path / "out" / "linkdir-app"
        app_dir.mkdir(parents=True)
        (app_dir / "assets").symlink_to(outside, target_is_directory=True)

        scaffold_app(tmp_path / "out", "linkdir-app")
        assert not (outside / "icon.png").exists(), "write escaped the app directory"

    def test_icon_ships_without_the_optional_ui(self, tmp_path):
        """The store icon is about being LISTED, not about having a UI, so it
        must not ride along on `include_ui`."""
        app_dir = scaffold_app(tmp_path, "headless-app")
        assert not (app_dir / "ui").exists()
        assert (app_dir / "assets" / "icon.png").is_file()

    def test_readme_points_at_the_placeholder(self, tmp_path):
        """The generated tree is where a developer learns the file is theirs to
        replace; a placeholder nobody knows to replace ships as the real icon."""
        readme = (scaffold_app(tmp_path, "tree-app") / "README.md").read_text(
            encoding="utf-8"
        )
        assert "assets/" in readme
        assert "replace this placeholder" in readme

    def test_scaffold_with_backend(self, tmp_path):
        app_dir = scaffold_app(tmp_path, "backend-app", include_backend=True)
        assert (app_dir / "backend" / "server.py").is_file()
        m = AppManifest.from_json_file(app_dir / "app.json")
        assert m.backend.entryPoint == "backend/server.py"

    def test_scaffold_without_backend(self, tmp_path):
        app_dir = scaffold_app(tmp_path, "no-backend")
        assert not (app_dir / "backend").exists()

    def test_custom_metadata(self, tmp_path):
        app_dir = scaffold_app(
            tmp_path, "custom-app",
            display_name="Custom App",
            description="A custom description",
            author="testuser",
        )
        m = AppManifest.from_json_file(app_dir / "app.json")
        assert m.displayName == "Custom App"
        assert m.description == "A custom description"
        assert m.author == "testuser"

    def test_agent_is_valid_json(self, tmp_path):
        app_dir = scaffold_app(tmp_path, "json-check")
        agent = json.loads((app_dir / "agents" / "sample-agent.json").read_text(encoding="utf-8"))
        assert agent["name"] == "sample-agent"
        assert "model" in agent

    def test_skill_has_frontmatter(self, tmp_path):
        app_dir = scaffold_app(tmp_path, "skill-check")
        content = (app_dir / "skills" / "sample-skill" / "SKILL.md").read_text(encoding="utf-8")
        assert "---" in content
        assert "description:" in content

    def test_readme_has_name(self, tmp_path):
        app_dir = scaffold_app(tmp_path, "readme-check")
        readme = (app_dir / "README.md").read_text(encoding="utf-8")
        assert "readme-check" in readme
        assert "kirocrew app install" in readme

    def test_scaffold_installable(self, tmp_path, monkeypatch):
        """Scaffolded app can be installed by the app manager."""
        home = tmp_path / "kirocrew-home"
        home.mkdir()
        monkeypatch.setenv("KIROCREW_HOME", str(home))

        app_dir = scaffold_app(tmp_path / "output", "installable-app")
        from kiro_crew.apps.manager import install_app
        result = install_app(app_dir)
        assert result.ok, result.error

    def test_scaffold_cli_integration(self, tmp_path, monkeypatch, capsys):
        """Test the CLI init command via _handle_app."""
        home = tmp_path / "kirocrew-home"
        home.mkdir()
        monkeypatch.setenv("KIROCREW_HOME", str(home))

        import argparse

        from kiro_crew.cli_commands import _handle_app
        ns = argparse.Namespace(app_action="init", name="cli-scaffolded", dir=str(tmp_path), backend=False)
        _handle_app(ns)
        captured = capsys.readouterr()
        assert "Scaffolded" in captured.out
        assert (tmp_path / "cli-scaffolded" / "app.json").is_file()

    def test_scaffold_with_ui(self, tmp_path):
        """--ui generates ui/ directory with package.json, vite config, and App.tsx."""
        app_dir = scaffold_app(tmp_path, "ui-app", include_ui=True)
        assert (app_dir / "ui" / "package.json").is_file()
        assert (app_dir / "ui" / "vite.config.ts").is_file()
        assert (app_dir / "ui" / "src" / "App.tsx").is_file()
        assert (app_dir / "ui" / ".gitignore").is_file()

        # package.json should reference the app name
        pkg = json.loads((app_dir / "ui" / "package.json").read_text(encoding="utf-8"))
        assert pkg["name"] == "ui-app-ui"
        assert "react" in pkg["dependencies"]
        assert "vite" in pkg["devDependencies"]

        # vite config should externalize shared modules
        vite_cfg = (app_dir / "ui" / "vite.config.ts").read_text(encoding="utf-8")
        assert "@kirocrew/app-sdk" in vite_cfg
        assert "@kirocrew/app-sdk/ui" in vite_cfg

        # App.tsx should have a valid component
        app_tsx = (app_dir / "ui" / "src" / "App.tsx").read_text(encoding="utf-8")
        assert "useAppApi" in app_tsx
        assert "PageHeader" in app_tsx

    def test_scaffold_with_ui_manifest_valid(self, tmp_path):
        """--ui scaffold produces a valid manifest with ui fields."""
        app_dir = scaffold_app(tmp_path, "ui-valid", include_ui=True)
        m = AppManifest.from_json_file(app_dir / "app.json")
        assert m.validate() == []
        assert m.ui.entry == "dist/index.mjs"
        assert len(m.ui.pages) == 1
        assert m.ui.pages[0].route == "/apps/ui-valid"

    def test_scaffold_without_ui(self, tmp_path):
        """Without --ui, no ui/ directory is created."""
        app_dir = scaffold_app(tmp_path, "no-ui")
        assert not (app_dir / "ui").exists()

    def test_scaffold_with_cron(self, tmp_path):
        """--cron generates a sample cron entry in app.json."""
        app_dir = scaffold_app(tmp_path, "cron-app", include_cron=True)
        m = AppManifest.from_json_file(app_dir / "app.json")
        assert m.validate() == []
        assert len(m.crons) == 1
        assert m.crons[0].name == "cron-app-check"
        assert m.crons[0].every == 300

    def test_scaffold_without_cron(self, tmp_path):
        """Without --cron, no crons in manifest."""
        app_dir = scaffold_app(tmp_path, "no-cron")
        m = AppManifest.from_json_file(app_dir / "app.json")
        assert len(m.crons) == 0

    def test_scaffold_all_options(self, tmp_path):
        """All flags together produce a valid manifest."""
        app_dir = scaffold_app(
            tmp_path, "full-app",
            include_backend=True, include_ui=True, include_cron=True,
        )
        m = AppManifest.from_json_file(app_dir / "app.json")
        assert m.validate() == []
        assert m.backend.entryPoint == "backend/server.py"
        assert m.ui.entry == "dist/index.mjs"
        assert len(m.crons) == 1
        assert (app_dir / "backend" / "server.py").is_file()
        assert (app_dir / "ui" / "src" / "App.tsx").is_file()
