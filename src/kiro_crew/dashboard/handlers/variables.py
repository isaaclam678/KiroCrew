"""Dashboard handlers for the crew-variables cascade.

``GET`` reports what this endpoint can EDIT — the pairs ``config.json`` itself
holds for each scope — alongside the resolved map, where each winning value came
from, and which keys ``config.local.json`` supplies.

``PUT`` applies a PER-KEY patch: ``set`` names pairs to write, ``delete`` names
keys to remove. It deliberately does not replace a whole scope. The replace form
drew a data-loss finding in three consecutive review rounds, every one of them
from the same root — the client had to echo back a map it had read from the MERGED
config, so a base value shadowed by the overlay was invisible in that read and got
dropped, two clients replacing the same scope clobbered each other's unrelated
edits, and overlay-owned pairs rode along and had to be subtracted back out.
Touching only the named keys removes all three: a key nobody named is never read,
never rewritten, and cannot be lost.

Deleting is therefore an explicit verb rather than "absence from the map", which
also keeps the empty string unambiguous — it is a legal value that still overrides
a broader scope, so it cannot share an encoding with "unset".

Validation refuses rather than drops. The config loader deliberately drops a bad
pair with a warning so one hand-edited mistake cannot cost the rest of a scope or
fail a load, but a dashboard write is interactive: silently discarding a pair the
user just typed would look like a save that worked.
"""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path

from aiohttp import web

from kiro_crew.config.loader import (
    ConfigReadError,
    KiroCrewConfig,
    config_local_path,
    config_path,
    resolve_variables,
    update_config_locked,
)
from kiro_crew.sel import sel
from kiro_crew.variables import validate_pair

logger = logging.getLogger(__name__)

SCOPE_GLOBAL = "global"
SCOPE_WORKSPACE = "workspace"
_WRITABLE_SCOPES = (SCOPE_GLOBAL, SCOPE_WORKSPACE)


def _read_overlay() -> dict:
    """The raw ``config.local.json`` document, or ``{}``.

    Blocking; call from a thread. Never raises: an unreadable or malformed overlay
    is treated as absent, matching :meth:`KiroCrewConfig.save`, which swallows the
    same two errors rather than refusing to persist the base config.
    """
    local_path = config_local_path()
    if not local_path.is_file():
        return {}
    try:
        raw = json.loads(local_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}
    return raw if isinstance(raw, dict) else {}


def _base_state(path: Path, workspace: str) -> tuple[dict, bool]:
    """The overlay document, and whether *workspace* is a key in the BASE mapping.

    Both are read from disk rather than from ``KiroCrewConfig.load()`` because that
    returns the two files merged, and this endpoint writes only the base one.
    Blocking; call from a thread.
    """
    overlay = _read_overlay()
    present = False
    if workspace:
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            raw = {}
        workspaces = raw.get("workspaces") if isinstance(raw, dict) else None
        present = isinstance(workspaces, dict) and workspace in workspaces
    return overlay, present


def _overlay_keys(overlay: dict, scope: str, workspace: str) -> set[str]:
    """Variable names this scope inherits from ``config.local.json``."""
    if scope == SCOPE_GLOBAL:
        owned = overlay.get("variables")
    else:
        workspaces = overlay.get("workspaces")
        entry = workspaces.get(workspace) if isinstance(workspaces, dict) else None
        owned = entry.get("variables") if isinstance(entry, dict) else None
    return set(owned) if isinstance(owned, dict) else set()


class _WorkspaceVanished(Exception):
    """The target workspace was deleted between the pre-check and the locked write.

    Raised from inside the ``update_config_locked`` mutate callback. The callback
    is invoked unguarded inside the lock hold, so raising aborts the write before
    ``write_config_atomically`` runs and releases the lockfile on the way out —
    which is what makes refusal, rather than a resurrecting write, expressible
    from in there at all.
    """


def _view_inputs() -> tuple[KiroCrewConfig, dict, dict]:
    """The merged config, the raw overlay, and the raw BASE document.

    All three are needed because they answer different questions: the merged config
    says what a session RESOLVES, the base document says what this endpoint can
    EDIT, and the overlay says which keys it cannot. Blocking; call from a thread.
    """
    base: dict = {}
    try:
        raw = json.loads(config_path().read_text(encoding="utf-8"))
        if isinstance(raw, dict):
            base = raw
    except (json.JSONDecodeError, OSError):
        base = {}
    return KiroCrewConfig.load(), _read_overlay(), base


def _base_pairs(base: dict, scope: str, workspace: str = "") -> dict:
    """The pairs config.json itself holds for one scope."""
    if scope == SCOPE_GLOBAL:
        own = base.get("variables")
        return dict(own) if isinstance(own, dict) else {}
    workspaces = base.get("workspaces")
    entry = workspaces.get(workspace) if isinstance(workspaces, dict) else None
    own = entry.get("variables") if isinstance(entry, dict) else None
    return dict(own) if isinstance(own, dict) else {}


def _view(cfg: KiroCrewConfig, overlay: dict, base: dict) -> dict:
    """What this endpoint can edit, plus the resolved map for the active context.

    ``global`` and ``workspaces`` report the BASE document's own pairs, not the
    merged ones. Reporting merged values as editable is what let the panel show a
    pair it did not own: the user edited a value the overlay supplies, the write
    was inert or destructive, and a base value hidden behind an overlay key was
    invisible entirely.
    """
    resolution = resolve_variables(cfg)
    return {
        "global": _base_pairs(base, SCOPE_GLOBAL),
        "workspaces": {name: _base_pairs(base, SCOPE_WORKSPACE, name) for name in cfg.workspaces},
        "crews": {name: dict(agent.variables) for name, agent in cfg.agents.items()},
        "effective": dict(resolution.values),
        "winning_scope": dict(resolution.winning_scope),
        "shadowed": {key: list(scopes) for key, scopes in resolution.shadowed.items()},
        "active_workspace": resolution.workspace_name,
        "active_agent": resolution.agent_name,
        # Keys supplied by config.local.json. This endpoint writes config.json
        # alone, so it REFUSES a set or delete naming one of these: the overlay
        # wins on load, so a base write would be inert and a delete would drop a
        # key the base may not hold while the overlay keeps re-supplying it.
        "overlay_owned": {
            "global": sorted(_overlay_keys(overlay, SCOPE_GLOBAL, "")),
            "workspaces": {
                name: sorted(_overlay_keys(overlay, SCOPE_WORKSPACE, name))
                for name in cfg.workspaces
                if _overlay_keys(overlay, SCOPE_WORKSPACE, name)
            },
        },
    }


async def api_variables(request: web.Request) -> web.Response:
    """GET/PUT /api/variables — read the cascade, or replace one scope."""
    if request.method != "PUT":
        return web.json_response(_view(*(await asyncio.to_thread(_view_inputs))))

    caller = request.get("user", "dashboard")

    def _deny(code: str, error: str) -> web.Response:
        """Refuse a malformed request.

        The status is a literal rather than a parameter: the error-code contract
        gate counts a computed ``status=`` separately precisely because hoisting
        it into a variable would defeat the static check, and every refusal on
        this path is a 400 anyway.
        """
        sel().log_api_access(
            caller=caller,
            operation="variables.update",
            outcome="denied",
            error=error,
        )
        return web.json_response({"error": error, "code": code}, status=400)

    try:
        body = await request.json()
    except Exception:
        return _deny("variables_invalid_json", "invalid JSON")
    if not isinstance(body, dict):
        return _deny("variables_invalid_body", "body must be an object")

    scope = body.get("scope")
    if scope not in _WRITABLE_SCOPES:
        return _deny(
            "variables_invalid_scope",
            f"scope must be one of {', '.join(_WRITABLE_SCOPES)}",
        )

    raw_set = body.get("set")
    raw_delete = body.get("delete")
    if raw_set is None and raw_delete is None:
        return _deny(
            "variables_invalid_values",
            "body must carry 'set' (object) and/or 'delete' (array of names)",
        )
    if raw_set is not None and not isinstance(raw_set, dict):
        return _deny("variables_invalid_values", "set must be an object")
    if raw_delete is not None and not isinstance(raw_delete, list):
        return _deny("variables_invalid_values", "delete must be an array of names")

    values: dict[str, str] = {}
    for key, value in (raw_set or {}).items():
        name, outcome = validate_pair(key, value)
        if name is None:
            sel().log_api_access(
                caller=caller,
                operation="variables.update",
                outcome="denied",
                error=f"invalid variable: {outcome}",
            )
            return web.json_response(
                {"error": outcome, "code": "variables_invalid_pair", "key": str(key)},
                status=400,
            )
        values[name] = outcome

    removals: list[str] = []
    for key in raw_delete or []:
        # A delete names a key rather than carrying a value, so it is validated by
        # the same grammar with a throwaway value: an unparseable name could not
        # have been stored by this endpoint in the first place.
        name, outcome = validate_pair(key, "")
        if name is None:
            # Through the audited helper, like every sibling refusal: the set-side
            # rejection below logs one, so a delete that returned 400 with no SEL
            # entry would leave a refusal invisible in the audit trail.
            sel().log_api_access(
                caller=caller,
                operation="variables.update",
                outcome="denied",
                error=f"invalid variable name in delete: {outcome}",
            )
            return web.json_response(
                {"error": outcome, "code": "variables_invalid_pair", "key": str(key)},
                status=400,
            )
        removals.append(name)

    overlapping = sorted(set(values) & set(removals))
    if overlapping:
        return _deny(
            "variables_conflicting_change",
            f"a key cannot be set and deleted in one request: {', '.join(overlapping)}",
        )

    cfg = KiroCrewConfig.load()
    workspace = body.get("workspace") or ""
    if scope == SCOPE_WORKSPACE:
        if not isinstance(workspace, str) or workspace not in cfg.workspaces:
            return _deny(
                "variables_unknown_workspace",
                f"unknown workspace: {workspace!r}",
            )

    path = config_path()

    # ``cfg`` above is the MERGED view: KiroCrewConfig.load() deep-merges
    # config.local.json over config.json, and this endpoint writes config.json
    # ALONE. Read authority and write target are different documents, so the base
    # document's own prior state is read here, off-loop, and decides below.
    overlay, base_had_workspace = await asyncio.to_thread(
        _base_state, path, workspace if scope == SCOPE_WORKSPACE else ""
    )

    # A key the overlay supplies cannot be changed from here: config.local.json
    # wins on load, so a write to the base would be inert and a delete would drop a
    # key the base may not even hold while the overlay keeps re-supplying it. The
    # GET view reports these as ``overlay_owned``; refusing is what makes that
    # report honest instead of decorative.
    owned = _overlay_keys(overlay, scope, workspace)
    blocked = sorted(owned & (set(values) | set(removals)))
    if blocked:
        return _deny(
            "variables_overlay_owned",
            "these variables come from config.local.json and cannot be changed "
            f"here: {', '.join(blocked)}",
        )

    def _mutate(data: dict) -> dict:
        """Apply this request's PER-KEY changes inside the locked critical section.

        A patch, deliberately not a whole-scope replace. The replace form produced
        three separate data-loss findings in three review rounds, all from the same
        root: the client had to echo back a whole map it had read from the MERGED
        config, so

          * a base value shadowed by the overlay was invisible in that read, came
            back as the overlay's value, and was then dropped as "unchanged" —
            deleting a value the user never touched;
          * two clients each replacing the whole scope clobbered each other's
            unrelated edits;
          * overlay-owned pairs rode along and had to be subtracted back out.

        Touching only the named keys removes all three at once: a key nobody named
        is never read, never rewritten, and cannot be lost.
        """
        if scope == SCOPE_GLOBAL:
            target = data.get("variables")
            if not isinstance(target, dict):
                target = {}
                data["variables"] = target
            for name, value in values.items():
                target[name] = value
            for name in removals:
                target.pop(name, None)
            return data
        raw_workspaces = data.get("workspaces")
        # The isinstance is repeated rather than derived into a bool: a bool does
        # not narrow a type, so deriving it leaves the value as
        # ``dict | Any | None`` and the assignments below fail to type-check.
        workspaces: dict = raw_workspaces if isinstance(raw_workspaces, dict) else {}
        if not isinstance(raw_workspaces, dict):
            data["workspaces"] = workspaces
        entry = workspaces.get(workspace)
        if isinstance(entry, str):
            # The legacy flat form maps a workspace name straight to its directory.
            # Widening it in place keeps the directory the operator set; assigning
            # a key onto the string would raise.
            entry = {"dir": entry}
            workspaces[workspace] = entry
        elif entry is None and base_had_workspace:
            # The workspace WAS a key in config.json's own mapping when this
            # request started and is gone now, so a concurrent writer deleted it
            # mid-flight. Recreating it would resurrect a workspace the operator
            # just removed, rebuilt from ``fallback_dir`` — a directory read before
            # the lock and therefore already stale. Refuse.
            #
            # Absence WITHOUT that prior presence is not a deletion: it is a first
            # write, or a workspace that lives in the overlay, and both must
            # materialize normally.
            raise _WorkspaceVanished
        elif not isinstance(entry, dict):
            # Materializing an entry so this scope's variables have somewhere to
            # live. Deliberately WITHOUT a ``dir``.
            #
            # This branch is reachable only for a workspace config.json does not
            # list — a PUT requires membership in the merged view, so the workspace
            # is supplied by config.local.json. ``fallback_dir`` is therefore an
            # OVERLAY-derived value, and writing it here would copy another file's
            # directory into the base: deleting the workspace from the overlay
            # later would resurrect it from the base with a stale path. The deep
            # merge supplies the real directory on every load, so omitting it keeps
            # config.local.json the single owner of what it declared.
            entry = {}
            workspaces[workspace] = entry
        target = entry.get("variables")
        if not isinstance(target, dict):
            target = {}
            entry["variables"] = target
        for name, value in values.items():
            target[name] = value
        for name in removals:
            target.pop(name, None)
        return data

    # update_config_locked is the required path for a new config.json mutation: it
    # holds an advisory lock across the whole read-modify-write, so a concurrent CLI
    # or dashboard write cannot land between the read and the rename and have its
    # settings deleted by this whole-file replacement. It also preserves the file's
    # permission bits. Offloaded because it reads, locks and fsyncs — blocking work
    # that must not run on the gateway event loop.
    try:
        await asyncio.to_thread(update_config_locked, path, mutate=_mutate)
    except _WorkspaceVanished:
        return _deny(
            "variables_unknown_workspace",
            f"unknown workspace: {workspace!r}",
        )
    except ConfigReadError:
        sel().log_api_access(
            caller=caller,
            operation="variables.update",
            outcome="error",
            error="config.json is corrupt",
        )
        return web.json_response(
            {"error": "config.json is corrupt", "code": "config_corrupt"}, status=500
        )

    sel().log_api_access(
        caller=caller,
        operation="variables.update",
        outcome="ok",
        resources=f"{scope}:{workspace}" if scope == SCOPE_WORKSPACE else scope,
    )
    return web.json_response({"ok": True, **_view(*(await asyncio.to_thread(_view_inputs)))})
