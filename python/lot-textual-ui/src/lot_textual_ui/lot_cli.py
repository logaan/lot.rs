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
import os
from collections.abc import Mapping, Sequence
from typing import Any

import yaml

from .models import ComputedState, ThingList, Update

# --- parsing (pure, fixture-testable) --------------------------------------


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

    async def _run(self, *args: str) -> str:
        """Run ``lot <args>`` and return stdout, raising :class:`LotError`.

        ``env=None`` makes the child inherit this process's environment, which
        is how ``LOT_VAULT_PATH`` is honoured; an explicit ``env`` overrides it.
        """
        proc = await asyncio.create_subprocess_exec(
            self._lot_bin,
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=self._env,
            cwd=self._cwd,
        )
        stdout, stderr = await proc.communicate()
        if proc.returncode != 0:
            raise LotError(args, proc.returncode or -1, stderr.decode())
        return stdout.decode()

    # Read commands (Phase 1). Later phases extend this class with `watch`,
    # `config get`, and mutation wrappers; they must live here too.

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
