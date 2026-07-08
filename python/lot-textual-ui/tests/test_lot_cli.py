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
    parse_config,
    parse_help,
    parse_thing_list,
    parse_updates,
)
from lot_textual_ui.models import (
    ComputedState,
    EffectiveConfig,
    Thing,
    ThingList,
    Update,
    UpdateType,
    VaultEntry,
    default_update_types,
)

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


def test_config_parses_all_fields() -> None:
    # The full merged config is parsed (theme, keybindings, vaults, vault-path),
    # not just the theme — the keybinding-override and vault-switch work items
    # reuse this same model.
    config = parse_config(fixture("config_get.yaml"))
    assert isinstance(config, EffectiveConfig)
    assert config.theme == "nord"
    assert config.keybindings == {"quit": "Q", "new_thing": "n"}
    assert config.vaults == [
        VaultEntry(path="~/lot-vault", name="Personal"),
        VaultEntry(path="/srv/shared-vault", name=None),
    ]
    assert config.vault_path == "/Users/you/lot-vault"
    # The full effective update-type set is parsed in its configured order,
    # each entry with its takes-body/terminal flags, and the default update
    # type comes along with it.
    assert [t.name for t in config.update_types] == [
        "note",
        "work",
        "info",
        "done",
        "blocked",
        "wont-do",
    ]
    wont_do = config.update_types[-1]
    assert wont_do == UpdateType(name="wont-do", takes_body=False, terminal=True)
    assert config.default_update_type == "note"


def test_config_without_update_types_falls_back_to_the_stock_set() -> None:
    # An older `lot` without the update-types key still yields a usable set,
    # and the default update type falls back to `note`.
    config = parse_config("theme: null\nvault-path: /v\n")
    assert config.update_types == default_update_types()
    assert config.default_update_type == "note"


def test_config_get_runs_subcommand_and_parses(tmp_path: Path) -> None:
    payload = fixture("config_get.yaml").replace("'", "'\\''")
    args_file = tmp_path / "argv"
    fake = _write_fake_lot(
        tmp_path,
        f"#!/bin/sh\nprintf '%s' \"$*\" > \"$ARGV_OUT\"\nprintf '%s' '{payload}'\n",
    )
    env = {**os.environ, "ARGV_OUT": str(args_file)}
    cli = LotCli(lot_bin=fake, env=env)

    config = asyncio.run(cli.config_get())

    assert args_file.read_text() == "settings get"
    assert config.theme == "nord"


def test_settings_set_theme_runs_subcommand(tmp_path: Path) -> None:
    args_file = tmp_path / "argv"
    fake = _write_fake_lot(
        tmp_path,
        '#!/bin/sh\nprintf \'%s\' "$*" > "$ARGV_OUT"\n'
        "printf '%s' 'set theme = \"ansi-dark\" in /x/config.toml'\n",
    )
    env = {**os.environ, "ARGV_OUT": str(args_file)}
    cli = LotCli(lot_bin=fake, env=env)

    note = asyncio.run(cli.settings_set_theme("ansi-dark"))

    assert args_file.read_text() == "settings set theme ansi-dark"
    assert note == 'set theme = "ansi-dark" in /x/config.toml'


# --- tolerant parsing edge cases -------------------------------------------


def test_empty_documents_parse_to_empty() -> None:
    assert parse_thing_list("") == ThingList(path="", things=[])
    assert parse_updates("") == []
    assert parse_help("") == {}
    # An unset theme and empty collections when the config document is empty.
    assert parse_config("") == EffectiveConfig(
        theme=None, keybindings={}, vaults=[], vault_path=""
    )


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


def test_set_vault_path_targets_new_vault(tmp_path: Path) -> None:
    # After `set_vault_path`, the child sees LOT_VAULT_PATH set to the new vault
    # even when the adapter started with no explicit env (inheriting the process
    # environment). The fake echoes back whatever LOT_VAULT_PATH it was handed.
    fake = _write_fake_lot(
        tmp_path,
        '#!/bin/sh\nprintf "path: %s\\nthings: []\\n" "$LOT_VAULT_PATH"\n',
    )
    cli = LotCli(lot_bin=fake)
    cli.set_vault_path("/tmp/switched-vault")
    listing = asyncio.run(cli.thing_list())
    assert listing.path == "/tmp/switched-vault"


def test_set_vault_path_overrides_existing_env(tmp_path: Path) -> None:
    # An explicit LOT_VAULT_PATH in the adapter's env is replaced, not merged.
    fake = _write_fake_lot(
        tmp_path,
        '#!/bin/sh\nprintf "path: %s\\nthings: []\\n" "$LOT_VAULT_PATH"\n',
    )
    env = {**os.environ, "LOT_VAULT_PATH": "/old-vault"}
    cli = LotCli(lot_bin=fake, env=env)
    cli.set_vault_path("/new-vault")
    listing = asyncio.run(cli.thing_list())
    assert listing.path == "/new-vault"


def test_set_vault_path_drops_auto_commit_override(tmp_path: Path) -> None:
    # LOT_AUTO_COMMIT describes the vault the UI was *launched* for; switching
    # vaults must drop it so the new vault gets the default instead of
    # inheriting a setting that was never about it. The fake echoes the value
    # back, with `unset` marking an absent variable.
    fake = _write_fake_lot(
        tmp_path,
        '#!/bin/sh\nprintf "path: %s\\nthings: []\\n" "${LOT_AUTO_COMMIT-unset}"\n',
    )
    env = {**os.environ, "LOT_AUTO_COMMIT": "false"}
    cli = LotCli(lot_bin=fake, env=env)
    cli.set_vault_path("/new-vault")
    listing = asyncio.run(cli.thing_list())
    assert listing.path == "unset"


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


def test_thing_new_pipes_body_on_stdin_and_returns_id(tmp_path: Path) -> None:
    # A fake `lot` records the argv it saw and the stdin it was fed, then prints
    # the new id — proving the body goes on stdin (never as an argument) and the
    # name is split into trailing positional args.
    args_file = tmp_path / "argv"
    stdin_file = tmp_path / "stdin"
    fake = _write_fake_lot(
        tmp_path,
        '#!/bin/sh\nprintf \'%s\' "$*" > "$ARGV_OUT"\ncat > "$STDIN_OUT"\n'
        "printf 'lot:NEWID123'\n",
    )
    env = {
        **os.environ,
        "ARGV_OUT": str(args_file),
        "STDIN_OUT": str(stdin_file),
    }
    cli = LotCli(lot_bin=fake, env=env)

    new_id = asyncio.run(
        cli.thing_new("This is the name", "the body\nsecond line", parent="lot:par")
    )

    assert new_id == "lot:NEWID123"
    assert args_file.read_text() == "thing new --parent lot:par This is the name"
    assert stdin_file.read_text() == "the body\nsecond line"


def test_thing_new_without_parent_omits_the_flag(tmp_path: Path) -> None:
    args_file = tmp_path / "argv"
    fake = _write_fake_lot(
        tmp_path,
        '#!/bin/sh\nprintf \'%s\' "$*" > "$ARGV_OUT"\ncat > /dev/null\n'
        "printf 'lot:abc'\n",
    )
    env = {**os.environ, "ARGV_OUT": str(args_file)}
    cli = LotCli(lot_bin=fake, env=env)

    asyncio.run(cli.thing_new("Solo", ""))

    assert args_file.read_text() == "thing new Solo"


def test_thing_new_raises_on_nonzero_exit(tmp_path: Path) -> None:
    fake = _write_fake_lot(
        tmp_path,
        '#!/bin/sh\ncat > /dev/null\necho "nope" >&2\nexit 4\n',
    )
    cli = LotCli(lot_bin=fake)
    with pytest.raises(LotError) as excinfo:
        asyncio.run(cli.thing_new("Name", "body"))
    assert excinfo.value.returncode == 4
    assert "nope" in excinfo.value.stderr


def test_add_update_pipes_body_on_stdin_and_returns_id(tmp_path: Path) -> None:
    # A fake `lot` records argv and stdin, then prints the new update id —
    # proving the body goes on stdin (never as an argument) and the Thing is
    # targeted with `--thing <id>`.
    args_file = tmp_path / "argv"
    stdin_file = tmp_path / "stdin"
    fake = _write_fake_lot(
        tmp_path,
        '#!/bin/sh\nprintf \'%s\' "$*" > "$ARGV_OUT"\ncat > "$STDIN_OUT"\n'
        "printf 'lot:UPD1'\n",
    )
    env = {
        **os.environ,
        "ARGV_OUT": str(args_file),
        "STDIN_OUT": str(stdin_file),
    }
    cli = LotCli(lot_bin=fake, env=env)

    new_id = asyncio.run(cli.add_update("work", "lot:thing1", "did the work\nline two"))

    assert new_id == "lot:UPD1"
    assert args_file.read_text() == "update work --thing lot:thing1"
    assert stdin_file.read_text() == "did the work\nline two"


def test_add_update_targets_the_kinds_subcommand(tmp_path: Path) -> None:
    # The kind — built-in or custom — is the `lot update` subcommand.
    args_file = tmp_path / "argv"
    fake = _write_fake_lot(
        tmp_path,
        '#!/bin/sh\nprintf \'%s\' "$*" > "$ARGV_OUT"\ncat > /dev/null\n'
        "printf 'lot:UPD2'\n",
    )
    env = {**os.environ, "ARGV_OUT": str(args_file)}
    cli = LotCli(lot_bin=fake, env=env)

    new_id = asyncio.run(cli.add_update("info", "lot:thing1", "a result"))

    assert new_id == "lot:UPD2"
    assert args_file.read_text() == "update info --thing lot:thing1"


def test_add_update_with_no_body_sends_no_stdin(tmp_path: Path) -> None:
    # A bodyless marker type (`done`, or a custom takes-body=false type): the
    # fake fails if any stdin is fed to it, proving body=None writes none.
    args_file = tmp_path / "argv"
    fake = _write_fake_lot(
        tmp_path,
        '#!/bin/sh\nprintf \'%s\' "$*" > "$ARGV_OUT"\n'
        'if [ -n "$(cat)" ]; then echo "unexpected stdin" >&2; exit 9; fi\n'
        "printf 'lot:UPD3'\n",
    )
    env = {**os.environ, "ARGV_OUT": str(args_file)}
    cli = LotCli(lot_bin=fake, env=env)

    new_id = asyncio.run(cli.add_update("done", "lot:thing1", None))

    assert new_id == "lot:UPD3"
    assert args_file.read_text() == "update done --thing lot:thing1"


def test_add_update_runs_custom_types_like_builtins(tmp_path: Path) -> None:
    # A custom `wont-do` (takes-body=false, terminal=true) runs as
    # `lot update wont-do --thing <id>` with no stdin, exactly like `done`.
    args_file = tmp_path / "argv"
    fake = _write_fake_lot(
        tmp_path,
        '#!/bin/sh\nprintf \'%s\' "$*" > "$ARGV_OUT"\n'
        'if [ -n "$(cat)" ]; then echo "unexpected stdin" >&2; exit 9; fi\n'
        "printf 'lot:UPD4'\n",
    )
    env = {**os.environ, "ARGV_OUT": str(args_file)}
    cli = LotCli(lot_bin=fake, env=env)

    new_id = asyncio.run(cli.add_update("wont-do", "lot:thing1", None))

    assert new_id == "lot:UPD4"
    assert args_file.read_text() == "update wont-do --thing lot:thing1"


def test_thing_move_targets_parent_flag(tmp_path: Path) -> None:
    args_file = tmp_path / "argv"
    fake = _write_fake_lot(
        tmp_path,
        "#!/bin/sh\nprintf '%s' \"$*\" > \"$ARGV_OUT\"\nprintf 'lot:moved\\n'\n",
    )
    env = {**os.environ, "ARGV_OUT": str(args_file)}
    cli = LotCli(lot_bin=fake, env=env)

    moved = asyncio.run(cli.thing_move("lot:thing1", parent="lot:dest"))

    assert moved == "lot:moved"
    assert args_file.read_text() == "thing move lot:thing1 --parent lot:dest"


def test_thing_move_root_uses_root_flag(tmp_path: Path) -> None:
    args_file = tmp_path / "argv"
    fake = _write_fake_lot(
        tmp_path,
        "#!/bin/sh\nprintf '%s' \"$*\" > \"$ARGV_OUT\"\nprintf 'lot:moved'\n",
    )
    env = {**os.environ, "ARGV_OUT": str(args_file)}
    cli = LotCli(lot_bin=fake, env=env)

    asyncio.run(cli.thing_move("lot:thing1", root=True))

    assert args_file.read_text() == "thing move lot:thing1 --root"


def test_thing_move_requires_exactly_one_destination(tmp_path: Path) -> None:
    cli = LotCli(lot_bin="/nonexistent-lot")
    # Neither destination, and both destinations, are programming errors caught
    # before any subprocess is spawned.
    with pytest.raises(ValueError):
        asyncio.run(cli.thing_move("lot:thing1"))
    with pytest.raises(ValueError):
        asyncio.run(cli.thing_move("lot:thing1", parent="lot:dest", root=True))


def test_thing_move_raises_with_cli_error_text(tmp_path: Path) -> None:
    fake = _write_fake_lot(
        tmp_path,
        '#!/bin/sh\necho "cannot move a Thing inside itself" >&2\nexit 2\n',
    )
    cli = LotCli(lot_bin=fake)
    with pytest.raises(LotError) as excinfo:
        asyncio.run(cli.thing_move("lot:thing1", root=True))
    assert "cannot move a Thing inside itself" in excinfo.value.stderr


def test_thing_archive_passes_id_and_returns_it(tmp_path: Path) -> None:
    args_file = tmp_path / "argv"
    fake = _write_fake_lot(
        tmp_path,
        "#!/bin/sh\nprintf '%s' \"$*\" > \"$ARGV_OUT\"\nprintf 'lot:gone\\n'\n",
    )
    env = {**os.environ, "ARGV_OUT": str(args_file)}
    cli = LotCli(lot_bin=fake, env=env)

    archived = asyncio.run(cli.thing_archive("lot:thing1"))

    assert archived == "lot:gone"
    assert args_file.read_text() == "thing archive lot:thing1"


def test_thing_archive_raises_with_cli_error_text(tmp_path: Path) -> None:
    # The auto-commit refusal (readme §5.1.6) must surface verbatim.
    fake = _write_fake_lot(
        tmp_path,
        '#!/bin/sh\necho "archive requires vault.auto-commit" >&2\nexit 2\n',
    )
    cli = LotCli(lot_bin=fake)
    with pytest.raises(LotError) as excinfo:
        asyncio.run(cli.thing_archive("lot:thing1"))
    assert "archive requires vault.auto-commit" in excinfo.value.stderr


def test_vault_archive_returns_the_archived_ids(tmp_path: Path) -> None:
    args_file = tmp_path / "argv"
    fake = _write_fake_lot(
        tmp_path,
        '#!/bin/sh\nprintf \'%s\' "$*" > "$ARGV_OUT"\n'
        "printf 'lot:one\\nlot:two\\n'\n",
    )
    env = {**os.environ, "ARGV_OUT": str(args_file)}
    cli = LotCli(lot_bin=fake, env=env)

    archived = asyncio.run(cli.vault_archive())

    assert archived == ["lot:one", "lot:two"]
    assert args_file.read_text() == "vault archive"


def test_vault_archive_with_nothing_done_returns_empty(tmp_path: Path) -> None:
    # No done Things: the CLI prints nothing (readme §5.4.2) -> an empty list.
    fake = _write_fake_lot(tmp_path, "#!/bin/sh\nexit 0\n")
    cli = LotCli(lot_bin=fake)

    assert asyncio.run(cli.vault_archive()) == []


def test_vault_archive_raises_with_cli_error_text(tmp_path: Path) -> None:
    # The auto-commit refusal (readme §5.4.2) must surface verbatim.
    fake = _write_fake_lot(
        tmp_path,
        '#!/bin/sh\necho "archive requires vault.auto-commit" >&2\nexit 2\n',
    )
    cli = LotCli(lot_bin=fake)
    with pytest.raises(LotError) as excinfo:
        asyncio.run(cli.vault_archive())
    assert "archive requires vault.auto-commit" in excinfo.value.stderr


def test_claude_send_passes_model_and_thing_id(tmp_path: Path) -> None:
    # A fake `lot` records argv and echoes a launch reference, proving
    # `claude send` targets the model sub-command with the Thing id as an
    # explicit positional (not the LOT_THING_ID fallback).
    args_file = tmp_path / "argv"
    fake = _write_fake_lot(
        tmp_path,
        "#!/bin/sh\nprintf '%s' \"$*\" > \"$ARGV_OUT\"\nprintf 'backgrounded'\n",
    )
    env = {**os.environ, "ARGV_OUT": str(args_file)}
    cli = LotCli(lot_bin=fake, env=env)

    output = asyncio.run(cli.claude_send("opus", "lot:thing1"))

    assert output == "backgrounded"
    assert args_file.read_text() == "claude send opus lot:thing1"


def test_claude_send_raises_on_nonzero_exit(tmp_path: Path) -> None:
    fake = _write_fake_lot(
        tmp_path,
        '#!/bin/sh\necho "no claude" >&2\nexit 7\n',
    )
    cli = LotCli(lot_bin=fake)
    with pytest.raises(LotError) as excinfo:
        asyncio.run(cli.claude_send("sonnet", "lot:thing1"))
    assert excinfo.value.returncode == 7
    assert "no claude" in excinfo.value.stderr


def test_update_raises_on_nonzero_exit(tmp_path: Path) -> None:
    fake = _write_fake_lot(
        tmp_path,
        '#!/bin/sh\ncat > /dev/null\necho "nope" >&2\nexit 5\n',
    )
    cli = LotCli(lot_bin=fake)
    with pytest.raises(LotError) as excinfo:
        asyncio.run(cli.add_update("work", "lot:thing1", "body"))
    assert excinfo.value.returncode == 5
    assert "nope" in excinfo.value.stderr


def test_watch_streams_framed_events_from_subprocess(tmp_path: Path) -> None:
    # A fake `lot watch` that emits the fixture's framed stream, then exits.
    payload = fixture("watch_stream.yaml").replace("'", "'\\''")
    fake = _write_fake_lot(tmp_path, f"#!/bin/sh\nprintf '%s' '{payload}'\n")
    cli = LotCli(lot_bin=fake)

    async def collect() -> list:
        return [event async for event in cli.watch()]

    events = asyncio.run(collect())
    assert [e.kind for e in events] == ["created", "deleted"]
    assert events[0].id == "lot:6Ic9Cg6kx0Xk2hQhVz3aBd"
    assert events[0].name == "This is the name"
    assert events[1].id == "lot:6Ic9Cg6kx0Xk2hQhVz3aBd"
    assert events[1].state is None


def _blocking_watch_fake(tmp_path: Path) -> str:
    # A fake `lot watch` that emits one event then blocks forever, exactly like
    # the real command after a single change: one leading-marker event, no
    # trailing marker, no EOF.
    stream = "---\nkind: modified\nid: lot:x\nname: X\nstatus: note\n"
    payload = stream.replace("'", "'\\''")
    return _write_fake_lot(
        tmp_path,
        f"#!/bin/sh\nprintf '%s' '{payload}'\nwhile true; do sleep 1; done\n",
    )


def test_watch_delivers_lone_event_before_stream_closes(tmp_path: Path) -> None:
    # The idle-flush must surface an isolated change without waiting for a next
    # event or EOF, so a lone vault edit shows up live.
    cli = LotCli(lot_bin=_blocking_watch_fake(tmp_path))

    async def first_event() -> str:
        agen = cli.watch()
        try:
            event = await agen.__anext__()
            return event.kind
        finally:
            await agen.aclose()

    assert asyncio.run(asyncio.wait_for(first_event(), timeout=15)) == "modified"


def test_watch_terminates_subprocess_when_consumer_stops(tmp_path: Path) -> None:
    # Closing the generator early must tear the still-running subprocess down.
    cli = LotCli(lot_bin=_blocking_watch_fake(tmp_path))

    async def take_one() -> None:
        agen = cli.watch()
        first = await agen.__anext__()
        assert first.kind == "modified"
        # Closing the generator runs its `finally`, terminating `lot`.
        await agen.aclose()

    asyncio.run(asyncio.wait_for(take_one(), timeout=15))
