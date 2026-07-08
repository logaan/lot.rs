"""Unit tests for the pure keybinding-override transform.

:func:`lot_textual_ui.keys.apply_overrides` is a pure function over the central
:data:`~lot_textual_ui.keys.ACTION_BINDINGS` table, so it is tested directly
here without booting the app (the runtime wiring is covered in ``test_app.py``).
"""

from __future__ import annotations

from textual.binding import Binding, BindingsMap

from lot_textual_ui.keys import (
    ACTION_BINDINGS,
    REMAPPABLE_ACTIONS,
    apply_overrides,
)


def _by_action(bindings: list[Binding]) -> dict[str, list[str]]:
    """Map each action to the keys bound to it, in order."""
    result: dict[str, list[str]] = {}
    for binding in bindings:
        result.setdefault(binding.action, []).append(binding.key)
    return result


def test_empty_overrides_returns_defaults_unchanged() -> None:
    result = apply_overrides(ACTION_BINDINGS, {})
    # Same content...
    assert _by_action(result) == _by_action(ACTION_BINDINGS)
    # ...but a fresh list — the module constant is never mutated.
    assert result is not ACTION_BINDINGS


def test_override_replaces_only_the_matching_action() -> None:
    result = apply_overrides(ACTION_BINDINGS, {"cursor_down": "s"})
    by_action = _by_action(result)
    assert by_action["cursor_down"] == ["s"]
    # Every other action keeps its default key.
    assert by_action["cursor_up"] == ["k"]
    assert by_action["quit"] == ["q"]


def test_override_preserves_description_show_and_flags() -> None:
    # ``focus_left`` has two bindings (``h`` and, hidden, ``backspace``); each
    # must keep its *own* ``show`` value after an override, not have it
    # collapsed to one shared value.
    original = [b for b in ACTION_BINDINGS if b.action == "focus_left"]
    result = apply_overrides(ACTION_BINDINGS, {"focus_left": "b"})
    overridden = [b for b in result if b.action == "focus_left"]
    # Both bindings for the action are remapped onto the new key...
    assert {b.key for b in overridden} == {"b"}
    # ...with each one's description and show flag carried through untouched,
    # in the same order as the source table.
    assert [b.description for b in overridden] == [b.description for b in original]
    assert [b.show for b in overridden] == [b.show for b in original]


def test_unknown_action_is_ignored() -> None:
    result = apply_overrides(ACTION_BINDINGS, {"not_an_action": "x"})
    assert _by_action(result) == _by_action(ACTION_BINDINGS)


def test_multiple_keys_per_action_split_in_a_bindings_map() -> None:
    # A comma-separated override binds several keys to one action; Textual's
    # BindingsMap splits them into one binding per key. ``1`` is an unbound key,
    # so the resulting map has it mapped to exactly the overridden action.
    result = apply_overrides(ACTION_BINDINGS, {"cursor_down": "1,down"})
    keys = BindingsMap(result).key_to_bindings
    assert "1" in keys
    assert "down" in keys
    assert all(b.action == "cursor_down" for b in keys["1"] + keys["down"])
    # The default ``j`` no longer maps to cursor_down.
    assert "j" not in keys or all(b.action != "cursor_down" for b in keys["j"])


def test_remappable_actions_matches_the_table() -> None:
    # The documented action list is exactly the set of actions in the table.
    assert set(REMAPPABLE_ACTIONS) == {b.action for b in ACTION_BINDINGS}
    # Core motions are remappable.
    assert {"quit", "cursor_down", "cursor_up", "focus_left", "focus_right"} <= set(
        REMAPPABLE_ACTIONS
    )
