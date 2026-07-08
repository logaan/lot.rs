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
from lot_textual_ui.mnemonics import assign_mnemonic


def test_first_letter_is_chosen_when_free() -> None:
    used: set[str] = set()
    key, markup = assign_mnemonic("Add", used)
    assert key == "ctrl+a"
    assert markup == "[underline]A[/underline]dd"
    # The chosen letter is recorded so a later call on the same screen avoids it.
    assert used == {"a"}


def test_globally_reserved_letters_are_skipped() -> None:
    # "Create" starts with "c" — one of the app-wide reserved ctrl letters
    # (ctrl+c/p/q/z already mean copy/palette/quit-adjacent/undo-ish) — so the
    # mnemonic must fall through to the next available letter instead.
    used: set[str] = set()
    key, markup = assign_mnemonic("Create", used)
    assert key == "ctrl+r"
    assert markup == "C[underline]r[/underline]eate"
    assert key not in {f"ctrl+{letter}" for letter in RESERVED_CTRL_LETTERS}


def test_letters_seeded_via_used_are_skipped() -> None:
    # A caller seeds `used` with letters the hosting screen already spends on
    # its own bindings (e.g. ctrl+s submit) before the first button call.
    used = {"a"}
    key, markup = assign_mnemonic("Add", used)
    assert key == "ctrl+d"
    assert markup == "A[underline]d[/underline]d"


def test_priority_order_avoids_collisions_between_two_buttons() -> None:
    # The higher-priority button (called first) picks its own best letter;
    # the lower-priority one's search then excludes whatever was just taken.
    used: set[str] = set()
    add_key, _add_markup = assign_mnemonic("Add", used)
    cancel_key, cancel_markup = assign_mnemonic("Cancel", used)

    assert add_key == "ctrl+a"
    assert cancel_key == "ctrl+n"
    assert cancel_key != add_key
    assert cancel_markup == "Ca[underline]n[/underline]cel"


def test_a_different_confirm_label_gets_its_own_live_assignment() -> None:
    # A runtime confirm_label ("Delete" rather than today's "Archive" call
    # sites) still resolves against the reserved/used sets fresh each time.
    used: set[str] = set()
    confirm_key, confirm_markup = assign_mnemonic("Delete", used)
    cancel_key, cancel_markup = assign_mnemonic("Cancel", used)

    assert confirm_key == "ctrl+d"
    assert confirm_markup == "[underline]D[/underline]elete"
    assert cancel_key == "ctrl+a"
    assert cancel_markup == "C[underline]a[/underline]ncel"


def test_non_letter_characters_are_skipped() -> None:
    used: set[str] = set()
    key, markup = assign_mnemonic("2FA", used)
    assert key == "ctrl+f"
    assert markup == "2[underline]F[/underline]A"


def test_matching_is_case_insensitive_against_reserved_and_used() -> None:
    used = {"a"}
    key, markup = assign_mnemonic("add", used)
    assert key == "ctrl+d"
    assert markup == "a[underline]d[/underline]d"


def test_raises_when_every_letter_is_reserved_or_used() -> None:
    # "c" is globally reserved; "a" and "b" are pre-claimed by the caller —
    # nothing in "abc" is left to pick.
    used = {"a", "b"}
    with pytest.raises(ValueError):
        assign_mnemonic("abc", used)
