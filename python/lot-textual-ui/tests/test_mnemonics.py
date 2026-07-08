"""Tests for the ctrl+letter button-mnemonic picker (:mod:`lot_textual_ui.mnemonics`).

These are pure-function tests of :func:`assign_mnemonic` itself — the
integration point (a real screen's buttons carrying the underlined label and
responding to the bound key even while an Input/TextArea has focus) is
covered per-screen in ``test_forms.py``, ``test_update_form.py`` and
``test_batch.py``.
"""

from __future__ import annotations

import pytest

from lot_textual_ui.command_nav import RESERVED_CTRL_LETTERS
from lot_textual_ui.forms import _SCREEN_RESERVED_LETTERS
from lot_textual_ui.mnemonics import (
    _EDITING_CTRL_LETTERS,
    _RESERVED_LETTERS,
    assign_mnemonic,
)


def test_first_free_letter_is_chosen() -> None:
    used: set[str] = set()
    key, markup = assign_mnemonic("Move", used)
    assert key == "ctrl+m"
    assert markup == "[underline]M[/underline]ove"
    # The chosen letter is recorded so a later call on the same screen avoids it.
    assert used == {"m"}


def test_globally_reserved_letters_are_skipped() -> None:
    # "Create" starts with "c" — one of the app-wide reserved ctrl letters
    # (ctrl+c/p/q/z already mean copy/palette/quit-adjacent/undo-ish) — so the
    # mnemonic must fall through to the next available letter instead.
    used: set[str] = set()
    key, markup = assign_mnemonic("Create", used)
    assert key == "ctrl+r"
    assert markup == "C[underline]r[/underline]eate"
    assert key not in {f"ctrl+{letter}" for letter in RESERVED_CTRL_LETTERS}


def test_reserved_set_is_navigation_plus_text_editing() -> None:
    # The mnemonic picker refuses two kinds of letter: the app-wide navigation
    # reservations (shared with command_nav) and the destructive text-editing
    # chords a focused Input/TextArea binds (plus ctrl+a).
    assert _EDITING_CTRL_LETTERS == frozenset("aukwxvy")
    assert _RESERVED_LETTERS == RESERVED_CTRL_LETTERS | _EDITING_CTRL_LETTERS
    # command_nav's own set stays the navigation-only reservation — the
    # editing chords are added *here*, never leaking back into it.
    assert not (_EDITING_CTRL_LETTERS & RESERVED_CTRL_LETTERS)


def test_editing_chords_are_reserved_so_add_skips_ctrl_a() -> None:
    # ctrl+a is a reserved editing chord (cursor-to-line-start / select-all
    # muscle memory), so "Add" can't take its own first letter — it falls to
    # "d". This is the safety fix: no mnemonic ever shadows a text-editing key.
    assert "a" in _EDITING_CTRL_LETTERS
    used: set[str] = set()
    key, markup = assign_mnemonic("Add", used)
    assert key == "ctrl+d"
    assert markup == "A[underline]d[/underline]d"


def test_letters_seeded_via_used_are_skipped() -> None:
    # A caller seeds `used` with letters the hosting screen already spends on
    # its own bindings (the forms seed ``{"s", "e", "o"}`` for their ctrl+s
    # submit / $EDITOR hatch) before the first button call. "Cancel" then skips
    # the reserved "c"/"a" and lands on "n".
    used = set(_SCREEN_RESERVED_LETTERS)
    key, markup = assign_mnemonic("Cancel", used)
    assert key == "ctrl+n"
    assert markup == "Ca[underline]n[/underline]cel"


def test_cancel_first_then_primary_stay_collision_free() -> None:
    # Cancel is assigned first on every screen; the primary action's search
    # then excludes Cancel's letter, so the two can never collide.
    used: set[str] = set()
    cancel_key, cancel_markup = assign_mnemonic("Cancel", used)
    add_key, _add_markup = assign_mnemonic("Add", used)

    assert cancel_key == "ctrl+n"
    assert cancel_markup == "Ca[underline]n[/underline]cel"
    assert add_key == "ctrl+d"
    assert add_key != cancel_key


def test_a_different_confirm_label_gets_its_own_live_assignment() -> None:
    # A runtime confirm_label ("Delete" rather than today's "Archive" call
    # sites) still resolves fresh — Cancel first, then the confirm action.
    used: set[str] = set()
    cancel_key, cancel_markup = assign_mnemonic("Cancel", used)
    confirm_key, confirm_markup = assign_mnemonic("Delete", used)

    assert cancel_key == "ctrl+n"
    assert cancel_markup == "Ca[underline]n[/underline]cel"
    assert confirm_key == "ctrl+d"
    assert confirm_markup == "[underline]D[/underline]elete"


def test_cancel_is_one_chord_everywhere_and_destructive_differs() -> None:
    """The core invariant: across the three modal screen shapes, Cancel is
    always the same chord, and no primary/destructive action ever equals it.

    Each block reproduces a real screen's seed and its Cancel-first order
    (New-Thing / New-Update seed their own ctrl+s/$EDITOR letters; the confirm
    dialog seeds nothing)."""
    new_thing = set(_SCREEN_RESERVED_LETTERS)
    cancel_thing, _ = assign_mnemonic("Cancel", new_thing)
    create_key, _ = assign_mnemonic("Create", new_thing)

    new_update = set(_SCREEN_RESERVED_LETTERS)
    cancel_update, _ = assign_mnemonic("Cancel", new_update)
    add_key, _ = assign_mnemonic("Add", new_update)

    confirm: set[str] = set()
    cancel_confirm, _ = assign_mnemonic("Cancel", confirm)
    archive_key, _ = assign_mnemonic("Archive", confirm)

    # Cancel resolves to the same chord on every screen type...
    assert cancel_thing == cancel_update == cancel_confirm == "ctrl+n"
    # ...and no submit/confirm chord — including the destructive Archive —
    # collides with the Cancel chord on any screen.
    for chord in (create_key, add_key, archive_key):
        assert chord != cancel_thing
    # Spell out the destructive case: ctrl+n never archives.
    assert archive_key == "ctrl+r"


def test_non_letter_characters_are_skipped() -> None:
    used: set[str] = set()
    key, markup = assign_mnemonic("2FA", used)
    assert key == "ctrl+f"
    assert markup == "2[underline]F[/underline]A"


def test_matching_is_case_insensitive_against_reserved() -> None:
    # Upper-case "A" still matches the lower-case reserved "a", so "Above"
    # skips its first letter for "b".
    used: set[str] = set()
    key, markup = assign_mnemonic("Above", used)
    assert key == "ctrl+b"
    assert markup == "A[underline]b[/underline]ove"


def test_matching_is_case_insensitive_against_used() -> None:
    used = {"m"}
    key, markup = assign_mnemonic("Move", used)
    assert key == "ctrl+o"
    assert markup == "M[underline]o[/underline]ve"


def test_raises_when_every_letter_is_reserved_or_used() -> None:
    # Every letter of "cav" is reserved: "c" (navigation) and "a"/"v" (the
    # text-editing chords) — nothing is left to pick.
    used: set[str] = set()
    with pytest.raises(ValueError):
        assign_mnemonic("cav", used)
