"""Unit tests for the LoT CLI adapter and its models.

Parsing is exercised against canned YAML fixtures (captured from the real CLI)
so no vault is needed. The async subprocess path is exercised with a tiny fake
``lot`` script, which also proves the environment is passed through.
"""

from __future__ import annotations

import asyncio
import os
import stat
from pathlib import Path

import pytest

from lot_textual_ui.lot_cli import (
    LotCli,
    LotError,
    parse_computed_state,
    parse_help,
    parse_thing_list,
    parse_updates,
)
from lot_textual_ui.models import ComputedState, Thing, ThingList, Update

FIXTURES = Path(__file__).parent / "fixtures"


def fixture(name: str) -> str:
    return (FIXTURES / name).read_text()


def walk(things: list[Thing]):
    for thing in things:
        yield thing
        yield from walk(thing.children)


# --- model parsing ---------------------------------------------------------


def test_thing_list_parses_nested_tree() -> None:
    listing = parse_thing_list(fixture("thing_list.yaml"))
    assert isinstance(listing, ThingList)
    assert listing.path
    assert listing.things

    # Find the "Plan" subtree and confirm hierarchy parses recursively.
    plan = next(t for t in walk(listing.things) if t.name == "Plan")
    assert isinstance(plan, Thing)
    assert plan.id.startswith("lot:")
    assert plan.children, "Plan should have child Things"
    phase1 = next(c for c in plan.children if c.name.startswith("Phase 1"))
    assert phase1.children
    assert any("CLI adapter" in gc.name for gc in phase1.children)


def test_thing_leaf_has_empty_children() -> None:
    listing = parse_thing_list(fixture("thing_list.yaml"))
    leaves = [t for t in listing.things if not t.children]
    assert leaves
    assert all(leaf.children == [] for leaf in leaves)


def test_computed_state_captures_timestamps_and_body() -> None:
    state = parse_computed_state(fixture("thing_get.yaml"))
    assert isinstance(state, ComputedState)
    assert state.status == "work"
    assert state.task_id.startswith("lot:")
    assert state.update_id.startswith("lot:")
    assert state.body and "Textual TUI" in state.body
    # Every *-at key is collected, none lost.
    assert "note-at" in state.timestamps
    assert "work-at" in state.timestamps
    assert "info-at" in state.timestamps
    assert all(key.endswith("-at") for key in state.timestamps)


def test_computed_state_done_thing_has_created_and_done_at() -> None:
    state = parse_computed_state(fixture("thing_get_done.yaml"))
    assert state.status == "done"
    assert "created-at" in state.timestamps
    assert "done-at" in state.timestamps


def test_updates_parse_all_types_and_keep_extra() -> None:
    updates = parse_updates(fixture("thing_updates.yaml"))
    assert updates
    assert all(isinstance(u, Update) for u in updates)
    types = {u.type for u in updates}
    assert {"note", "work", "info"} <= types

    first = updates[0]
    assert first.type == "note"
    assert first.at
    assert first.body
    # The first note carries a task-id in frontmatter; it must survive in extra.
    assert first.extra.get("task-id", "").startswith("lot:")


def test_help_parses_command_tree() -> None:
    tree = parse_help(fixture("help.yaml"))
    assert tree["name"] == "lot"
    names = {sub["name"] for sub in tree["subcommands"]}
    assert {"thing", "vault"} <= names


# --- tolerant parsing edge cases -------------------------------------------


def test_empty_documents_parse_to_empty() -> None:
    assert parse_thing_list("") == ThingList(path="", things=[])
    assert parse_updates("") == []
    assert parse_help("") == {}


def test_update_from_dict_preserves_unknown_keys() -> None:
    update = Update.from_dict(
        {
            "update-id": "lot:abc",
            "type": "work",
            "at": "2026-01-01T00:00:00+00:00",
            "body": "hi",
            "future-field": 42,
        }
    )
    assert update.extra == {"future-field": 42}


# --- async subprocess path -------------------------------------------------


def _write_fake_lot(tmp_path: Path, script: str) -> str:
    fake = tmp_path / "lot"
    fake.write_text(script)
    fake.chmod(fake.stat().st_mode | stat.S_IXUSR)
    return str(fake)


def test_run_invokes_subprocess_and_parses(tmp_path: Path) -> None:
    payload = fixture("thing_list.yaml").replace("'", "'\\''")
    fake = _write_fake_lot(
        tmp_path,
        f"#!/bin/sh\nprintf '%s' '{payload}'\n",
    )
    cli = LotCli(lot_bin=fake)
    listing = asyncio.run(cli.thing_list())
    assert isinstance(listing, ThingList)
    assert listing.things


def test_env_is_passed_through(tmp_path: Path) -> None:
    # The fake echoes back whatever LOT_VAULT_PATH it was handed.
    fake = _write_fake_lot(
        tmp_path,
        '#!/bin/sh\nprintf "path: %s\\nthings: []\\n" "$LOT_VAULT_PATH"\n',
    )
    env = {**os.environ, "LOT_VAULT_PATH": "/tmp/some-vault"}
    cli = LotCli(lot_bin=fake, env=env)
    listing = asyncio.run(cli.thing_list())
    assert listing.path == "/tmp/some-vault"


def test_nonzero_exit_raises_lot_error(tmp_path: Path) -> None:
    fake = _write_fake_lot(
        tmp_path,
        '#!/bin/sh\necho "boom" >&2\nexit 3\n',
    )
    cli = LotCli(lot_bin=fake)
    with pytest.raises(LotError) as excinfo:
        asyncio.run(cli.thing_get("lot:nope"))
    assert excinfo.value.returncode == 3
    assert "boom" in excinfo.value.stderr
