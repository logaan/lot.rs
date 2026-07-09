"""The single seam between the Textual UI and LoT.

Everything the UI knows about the vault flows through :class:`LotCli`: it runs
``lot`` subprocesses, parses their YAML, and returns typed models. No other
module may spawn ``lot`` or touch the vault.

Subprocesses run via :mod:`asyncio` so the Textual event loop never blocks. The
current process environment is passed through unchanged, so ``LOT_VAULT_PATH``
(and anything else ``lot`` reads) is honoured without this module interpreting
vault paths itself.

Parsing is split into module-level ``parse_*`` helpers so it can be unit-tested
against canned YAML fixtures without a real vault or subprocess.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
from collections.abc import AsyncIterator, Iterable, Iterator, Mapping, Sequence
from typing import Any

import yaml

from .models import ComputedState, EffectiveConfig, ThingList, Update, WatchEvent

# --- parsing (pure, fixture-testable) --------------------------------------


def parse_config(text: str) -> EffectiveConfig:
    """Parse ``lot settings get`` output into an :class:`EffectiveConfig`."""
    return EffectiveConfig.from_dict(yaml.safe_load(text) or {})


def parse_thing_list(text: str) -> ThingList:
    """Parse ``lot thing list --format=yaml`` output into a :class:`ThingList`."""
    return ThingList.from_dict(yaml.safe_load(text) or {})


def parse_computed_state(text: str) -> ComputedState:
    """Parse ``lot thing get`` output into a :class:`ComputedState`."""
    return ComputedState.from_dict(yaml.safe_load(text) or {})


def parse_updates(text: str) -> list[Update]:
    """Parse ``lot thing updates`` output into a list of :class:`Update`."""
    data = yaml.safe_load(text) or []
    return [Update.from_dict(entry) for entry in data]


def parse_help(text: str) -> dict[str, Any]:
    """Parse ``lot help --format=yaml`` into its raw command tree.

    Help drives the command palette dynamically, so it is returned as the plain
    nested mapping rather than a rigid model.
    """
    return yaml.safe_load(text) or {}


def parse_watch_event(text: str) -> WatchEvent:
    """Parse one ``lot watch`` YAML document into a :class:`WatchEvent`."""
    return WatchEvent.from_dict(yaml.safe_load(text) or {})


# --- watch-stream framing (pure, fixture-testable) -------------------------
#
# ``lot watch`` frames its stream by writing a bare ``---`` document marker at
# column 0 before each event, then the event body with *every* line indented
# (see ``lot help watch``). A body may itself contain a ``---`` line, but always
# indented
# inside a block scalar, so a ``---`` at column 0 unambiguously opens a new
# event. That lets a consumer split the live pipe one document at a time without
# waiting for the (never-ending) stream to close.


def _is_document_marker(line: str) -> bool:
    """True for a bare column-0 ``---`` line (an event boundary)."""
    return line.rstrip("\n") == "---"


class _WatchFramer:
    """Reassembles a line stream into whole YAML documents on ``---`` markers.

    Feed it lines with :meth:`push` (each returns a completed document, if the
    line closed one) and call :meth:`flush` at end-of-stream for any trailing
    document. Blank leading content before the first marker yields nothing.
    """

    def __init__(self) -> None:
        self._buf: list[str] = []

    def push(self, line: str) -> str | None:
        if _is_document_marker(line):
            return self.flush()
        self._buf.append(line)
        return None

    def flush(self) -> str | None:
        doc = "".join(self._buf) if any(part.strip() for part in self._buf) else None
        self._buf = []
        return doc


def iter_watch_documents(lines: Iterable[str]) -> Iterator[str]:
    """Yield each whole YAML document from an iterable of watch-stream lines."""
    framer = _WatchFramer()
    for line in lines:
        doc = framer.push(line)
        if doc is not None:
            yield doc
    tail = framer.flush()
    if tail is not None:
        yield tail


def parse_watch_stream(text: str) -> Iterator[WatchEvent]:
    """Parse a whole (captured) watch stream into :class:`WatchEvent`\\ s."""
    for document in iter_watch_documents(text.splitlines(keepends=True)):
        yield parse_watch_event(document)


# --- errors ----------------------------------------------------------------


class LotError(RuntimeError):
    """A ``lot`` invocation failed (non-zero exit)."""

    def __init__(self, args: Sequence[str], returncode: int, stderr: str) -> None:
        self.args_run = list(args)
        self.returncode = returncode
        self.stderr = stderr.strip()
        detail = f": {self.stderr}" if self.stderr else ""
        super().__init__(f"`lot {' '.join(args)}` exited with {returncode}{detail}")


# --- adapter ---------------------------------------------------------------


class LotCli:
    """Async adapter over the ``lot`` command-line interface.

    Args:
        lot_bin: The ``lot`` executable to invoke (name on ``PATH`` or a path).
        env: Environment for the subprocess. Defaults to the current process
            environment, so ``LOT_VAULT_PATH`` and friends are inherited. Pass
            an explicit mapping to override (e.g. in tests).
        cwd: Working directory for the subprocess. ``lot`` resolves its vault
            from config/env, not cwd, so this is rarely needed.
    """

    def __init__(
        self,
        lot_bin: str = "lot",
        env: Mapping[str, str] | None = None,
        cwd: str | os.PathLike[str] | None = None,
    ) -> None:
        self._lot_bin = lot_bin
        self._env = dict(env) if env is not None else None
        self._cwd = os.fspath(cwd) if cwd is not None else None

    def set_vault_path(self, path: str) -> None:
        """Retarget every *future* subprocess at the vault at ``path``.

        Sets ``LOT_VAULT_PATH`` in the environment handed to subsequently-spawned
        ``lot`` processes, so all later calls resolve the given vault instead of
        the ambient one — the mechanism behind the app's in-app vault switch. The
        base environment is this adapter's current ``env`` (the process
        environment when unset), so only ``LOT_VAULT_PATH`` changes; everything
        else the child inherits is preserved.

        This mutates the adapter **in place** on purpose: the whole UI shares one
        :class:`LotCli` instance (the app, the detail pane, the palette
        providers), so retargeting it here points every one of them at the new
        vault without re-wiring. Calls already in flight keep their old
        environment — only newly-spawned subprocesses see the change — so the app
        cancels the ``lot watch`` worker and reloads *after* calling this (see
        :meth:`~lot_textual_ui.app.LotTextualApp.action_switch_vault`).
        """
        base = self._env if self._env is not None else os.environ
        self._env = {**base, "LOT_VAULT_PATH": path}

    async def _exec(self, args: Sequence[str], *, stdin: str | None = None) -> str:
        """Spawn ``lot <args>`` and return stdout, raising :class:`LotError`.

        The one subprocess seam behind :meth:`_run` / :meth:`_run_with_stdin`.
        ``env=None`` makes the child inherit this process's environment, which
        is how ``LOT_VAULT_PATH`` is honoured; an explicit ``env`` overrides it.
        With ``stdin`` set, the child's stdin is opened as a pipe, the text is
        written to it and the pipe closed (``communicate(input=...)``), so
        ``lot`` sees EOF rather than blocking or opening an editor; with
        ``stdin=None`` no stdin pipe is opened at all.
        """
        proc = await asyncio.create_subprocess_exec(
            self._lot_bin,
            *args,
            stdin=asyncio.subprocess.PIPE if stdin is not None else None,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=self._env,
            cwd=self._cwd,
        )
        stdout, stderr = await proc.communicate(
            input=stdin.encode() if stdin is not None else None
        )
        if proc.returncode != 0:
            raise LotError(args, proc.returncode or -1, stderr.decode())
        return stdout.decode()

    async def _run(self, *args: str) -> str:
        """Run ``lot <args>`` and return stdout, raising :class:`LotError`."""
        return await self._exec(args)

    # Read commands (Phase 1). Later phases extend this class with `watch`,
    # `settings get`, and mutation wrappers; they must live here too.

    async def thing_list(self) -> ThingList:
        """Return the whole vault as a nested :class:`ThingList` tree."""
        return parse_thing_list(await self._run("thing", "list", "--format=yaml"))

    async def thing_get(self, thing_id: str) -> ComputedState:
        """Return the computed current state of a single Thing."""
        return parse_computed_state(await self._run("thing", "get", thing_id))

    async def thing_updates(self, thing_id: str) -> list[Update]:
        """Return a Thing's update thread as a list of :class:`Update`."""
        return parse_updates(await self._run("thing", "updates", thing_id))

    async def help_yaml(self) -> dict[str, Any]:
        """Return the ``lot`` command tree used to build the command palette."""
        return parse_help(await self._run("help", "--format=yaml"))

    async def config_get(self) -> EffectiveConfig:
        """Return the merged effective config from ``lot settings get``.

        Runs ``lot settings get`` (readme §5.5), whose default output is the
        merged user+vault config as YAML, and parses it into an
        :class:`EffectiveConfig`. The whole config is parsed — theme,
        keybindings, vaults and the resolved vault path — so the theme,
        keybinding-override and vault-switching work items all read config
        through this single seam rather than shelling out themselves. Raises
        :class:`LotError` on a non-zero exit (e.g. an older ``lot`` without the
        ``settings`` subcommand), which callers treat as "no config" and fall
        back to defaults.
        """
        return parse_config(await self._run("settings", "get"))

    async def settings_set_theme(self, name: str) -> str:
        """Persist the front-end theme to the user config, returning `lot`'s note.

        Runs ``lot settings set theme <name>`` (readme §5.5.2), which writes
        ``[tui].theme`` into the user-level config file so a runtime theme pick
        survives a restart. `lot` prints a one-line confirmation of what it wrote
        and where; the stripped line is returned. Raises :class:`LotError` on a
        non-zero exit (e.g. an older ``lot`` without ``settings set``), which the
        caller swallows so the live theme change still stands even if it cannot
        be persisted.
        """
        return (await self._run("settings", "set", "theme", name)).strip()

    async def thing_path(self, thing_id: str) -> str:
        """Return the filesystem path of a Thing's folder.

        Runs ``lot thing path <id>`` (readme §5.1.2) and returns the single
        printed path, stripped. Used by the "copy Thing path" action so path
        resolution stays inside this one seam rather than the UI shelling out.
        """
        return (await self._run("thing", "path", thing_id)).strip()

    async def update_path(self, update_id: str) -> str:
        """Return the filesystem path of a single Update file.

        Runs ``lot update path <update-id>`` (readme §5.2.4) and returns the
        printed path, stripped. Backs the "copy Update path" action; the CLI
        resolves the id across the whole vault, so the UI need only hand over the
        ``update-id`` it already holds from a Thing's update thread.
        """
        return (await self._run("update", "path", update_id)).strip()

    async def thing_new(
        self,
        name: str,
        body: str,
        parent: str | None = None,
        preamble: str | None = None,
    ) -> str:
        """Create a Thing and return its new ``lot:`` id.

        Runs ``lot thing new <name...>`` (the name split into trailing
        positional args, mirroring ``echo body | lot thing new This is the
        name``) with ``body`` fed on the child's **stdin** — never as a ``--``
        or trailing argument, which the CLI would reject and which would leave
        stdin dangling. ``parent`` maps to ``--parent <id>`` so the Thing is
        created as a child of an existing one (this is the seam the
        create-child-Things work item reuses). ``preamble`` maps to
        ``--preamble <yaml>``: extra frontmatter merged into the Thing's first
        update (e.g. ``claude-model: opus``).

        Every option must precede the name: ``lot thing new`` takes its name as
        a ``trailing_var_arg``, so a flag placed after it would be swallowed
        into the name rather than parsed.

        The body is written to the subprocess and its stdin is then closed
        (``communicate(input=...)``), so ``lot`` sees EOF and does not block
        waiting for more input or fall back to opening an editor. The command's
        sole output is the new Thing's id, which is returned stripped.
        """
        args: tuple[str, ...] = ("thing", "new")
        if parent is not None:
            args += ("--parent", parent)
        if preamble is not None:
            args += ("--preamble", preamble)
        # The name is passed as trailing positional args; clap joins them into
        # the Thing's name exactly like `lot thing new This is the name`.
        args += tuple(name.split())
        return (await self._run_with_stdin(body, *args)).strip()

    async def _run_with_stdin(self, stdin: str, *args: str) -> str:
        """Run ``lot <args>`` feeding ``stdin`` to the child, returning stdout.

        Like :meth:`_run` but with the child's stdin piped in (see
        :meth:`_exec`), so commands that read their content from standard
        input — notably ``lot thing new`` and ``lot update`` — get their body
        without an interactive editor. Raises :class:`LotError` on a non-zero
        exit, like :meth:`_run`.
        """
        return await self._exec(args, stdin=stdin)

    async def add_update(
        self,
        kind: str,
        thing_id: str,
        body: str | None,
        preamble: str | None = None,
    ) -> str:
        """Run ``lot update <kind> --thing <id>`` and return the new update id.

        The single seam for every Update type — types are entirely
        vault-configured (readme §1.3, §5.2.1) and the CLI treats every
        ``lot update <name>`` alike. ``kind`` is the update type's name; the
        caller picks it from the effective set discovered via
        :meth:`config_get`
        (:attr:`~lot_textual_ui.models.EffectiveConfig.update_types`).
        ``thing_id`` targets the Thing via the ``--thing`` option (never a
        trailing/``--`` argument, which the CLI would treat as content). When
        ``body`` is a string (a ``takes-body`` type, like the stock ``work``)
        it is fed on the child's stdin exactly like :meth:`thing_new`, so
        ``lot`` reads its content without opening an editor; when ``body`` is
        ``None`` (a bodyless marker type, like the stock ``done``) no stdin
        is written — the CLI would reject content for such a type. The
        command prints only the new update's id, which is returned stripped.
        ``preamble`` maps to ``--preamble <yaml>``: extra frontmatter merged
        into this update (e.g. ``claude-model: opus``). Raises
        :class:`LotError` on a non-zero exit (including an unknown type name,
        or a preamble that is not a YAML mapping or names a reserved key).
        """
        args = ("update", kind, "--thing", thing_id)
        if preamble is not None:
            args += ("--preamble", preamble)
        if body is None:
            return (await self._run(*args)).strip()
        return (await self._run_with_stdin(body, *args)).strip()

    async def thing_move(
        self, thing_id: str, parent: str | None = None, root: bool = False
    ) -> str:
        """Re-home a Thing (and its subtree) and return its id.

        Runs ``lot thing move <thing-id>`` (readme §5.1.7) with exactly one
        destination: ``parent`` maps to ``--parent <lot:id>`` (the Thing moves
        inside that Thing's folder) and ``root=True`` maps to ``--root`` (the
        Thing moves to the vault's top level). The CLI requires the destination
        to be explicit, so exactly one of the two must be given — anything else
        is a programming error and raises :class:`ValueError` before any
        subprocess is spawned. The CLI reports its own failures — unknown ids,
        a destination inside the moved subtree (a cycle), a no-op move, or a
        name collision at the destination — as single-line errors, surfaced
        here as :class:`LotError` for the batch-move flow to show per item.
        """
        if (parent is None) == (not root):
            raise ValueError("thing_move needs exactly one of parent= or root=True")
        destination = ("--root",) if root else ("--parent", str(parent))
        return (await self._run("thing", "move", thing_id, *destination)).strip()

    async def thing_archive(self, thing_id: str, *, force: bool = False) -> str:
        """Archive a Thing (and all its descendants) and return its id.

        Runs ``lot thing archive <thing-id>`` (readme §5.1.6), which commits
        the Thing's folder, commits its deletion, and only then removes it from
        disk. The CLI refuses when ``vault.auto-commit`` is ``false`` (history
        cannot be preserved without commits), and — unless ``force`` is set —
        also when the Thing has a not-done (non-terminal) descendant that would
        be deleted with it. Either refusal, like any other failure, surfaces as
        :class:`LotError` carrying the CLI's error text, which the batch-archive
        flow shows per item. ``force=True`` adds ``--force`` so the whole
        subtree is archived regardless (the UI asks first).
        """
        args = ("thing", "archive", thing_id)
        if force:
            args = (*args, "--force")
        return (await self._run(*args)).strip()

    async def vault_archive(self, *, force: bool = False) -> list[str]:
        """Archive every done Thing in the vault; return the archived ids.

        Runs ``lot vault archive`` (readme §5.4.2), which archives every Thing
        whose status is a terminal state (an update type with
        ``terminal = true``, like the stock ``done``) — committing each Thing,
        then committing all
        the deletions in one commit before removing anything from disk. The
        CLI prints one archived id per line (and nothing when the vault has no
        done Things, so this returns an empty list). Like ``thing_archive`` it
        refuses when ``vault.auto-commit`` is ``false``, and — unless ``force``
        is set — when a done Thing has a not-done descendant that would be swept
        away with it; those refusals surface as :class:`LotError`. ``force=True``
        adds ``--force`` so the sweep archives those subtrees too (the UI asks
        first).
        """
        args = ("vault", "archive")
        if force:
            args = (*args, "--force")
        return (await self._run(*args)).split()

    async def claude_send(self, model: str, thing_id: str) -> str:
        """Launch a background Claude session on a Thing via ``lot claude send``.

        Runs ``lot claude send <model> <thing-id>`` (readme §5.3), which spawns a
        background ``claude`` session loaded with the lot-task skill and records
        a ``work`` update on the Thing noting the launch. ``model`` selects the
        model sub-command (``sonnet``/``opus``/``fable``); ``thing_id`` is passed
        as the explicit positional rather than relying on the ``LOT_THING_ID``
        fallback, so the UI always sends the Thing the user picked. Returns the
        command's stdout — the ``claude --bg`` launch reference — which the
        caller may surface. Raises :class:`LotError` on a non-zero exit (e.g.
        ``claude`` not installed).
        """
        return await self._run("claude", "send", model, thing_id)

    async def run_command(self, *args: str) -> str:
        """Run an arbitrary ``lot`` subcommand and return its stdout.

        The generic escape hatch the command palette uses to invoke leaf
        commands discovered from ``lot help`` (e.g. ``run_command("thing",
        "list")``) without a bespoke typed method for each. Typed helpers above
        are preferred where they exist; this keeps every ``lot`` invocation
        inside :class:`LotCli` while letting new palette entries reuse one seam.
        Raises :class:`LotError` on a non-zero exit, like the typed helpers.
        """
        return await self._run(*args)

    # How long the stream may sit quiet before a buffered-but-unterminated
    # event is flushed. `lot watch` frames each event with a *leading* `---`,
    # so an event's body is only known-complete when the next event's marker
    # (or EOF) arrives. A lone change would therefore stay invisible until the
    # next one. Each event is written in a single flushed burst, so a short read
    # idle unambiguously marks its end: flush then, delivering isolated changes
    # promptly without ever splitting one event across two reads.
    _WATCH_IDLE_FLUSH = 0.1

    async def watch(self) -> AsyncIterator[WatchEvent]:
        """Stream vault changes as :class:`WatchEvent`\\ s from ``lot watch``.

        Spawns ``lot watch`` (inheriting the process environment the same way as
        :meth:`_run`, so ``LOT_VAULT_PATH`` is honoured), frames its stdout on
        column-0 ``---`` markers, and yields one parsed event per document as it
        settles off the live pipe. ``lot watch`` blocks forever, so this
        generator only ends when the caller stops consuming it (e.g. the worker
        is cancelled). Either way the subprocess is terminated on exit — via the
        ``finally`` below — so no orphan ``lot watch`` lingers.
        """
        proc = await asyncio.create_subprocess_exec(
            self._lot_bin,
            "watch",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=self._env,
            cwd=self._cwd,
        )
        stdout = proc.stdout
        assert stdout is not None
        framer = _WatchFramer()
        try:
            while True:
                try:
                    raw = await asyncio.wait_for(
                        stdout.readline(), timeout=self._WATCH_IDLE_FLUSH
                    )
                except TimeoutError:
                    # Quiet pipe: deliver whatever whole event is buffered.
                    doc = framer.flush()
                    if doc is not None:
                        yield parse_watch_event(doc)
                    continue
                if not raw:  # EOF: the stream closed.
                    break
                doc = framer.push(raw.decode())
                if doc is not None:
                    yield parse_watch_event(doc)
            tail = framer.flush()
            if tail is not None:
                yield parse_watch_event(tail)
            returncode = await proc.wait()
            if returncode != 0:
                stderr = b""
                if proc.stderr is not None:
                    stderr = await proc.stderr.read()
                raise LotError(("watch",), returncode, stderr.decode())
        finally:
            await self._terminate(proc)

    @staticmethod
    async def _terminate(proc: asyncio.subprocess.Process) -> None:
        """Best-effort teardown of a still-running subprocess."""
        if proc.returncode is not None:
            return
        with contextlib.suppress(ProcessLookupError):
            proc.terminate()
        try:
            await asyncio.wait_for(proc.wait(), timeout=5)
        except TimeoutError:
            with contextlib.suppress(ProcessLookupError):
                proc.kill()
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(proc.wait(), timeout=5)
