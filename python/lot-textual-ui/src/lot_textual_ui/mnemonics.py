"""Ctrl+letter keyboard mnemonics for modal buttons.

Every :class:`~textual.widgets.Button` in the app's modal forms
(:mod:`lot_textual_ui.forms`, :mod:`lot_textual_ui.batch`) gets a
``ctrl+<letter>`` shortcut derived from its own label, with that letter
underlined in the visible label so the shortcut is discoverable without a
footer hint. :func:`assign_mnemonic` is the one place that picks the letter,
so every screen applies the same rule instead of each hand-picking one.

The rule
--------

For each button, in **priority order** (the screen's more significant action
picks first — e.g. "Create" before "Cancel", "Archive" before "Cancel"):

1. Walk the label's characters left to right.
2. Skip anything that is not a letter, and anything (case-insensitively)
   already spoken for — either globally reserved
   (:data:`~lot_textual_ui.command_nav.RESERVED_CTRL_LETTERS`: ``ctrl+c``/
   ``p``/``q``/``z`` all already mean something else app-wide — see that
   module) or reserved by the *hosting screen itself* (its own ``ctrl+s``
   submit, ``ctrl+e`` ``$EDITOR`` hatch, etc — passed in via ``used``).
3. The first letter that clears both checks is the mnemonic; it is recorded
   into ``used`` (mutated in place) so the *next* button's search also avoids
   it.

This is a plain greedy pick, not a global optimiser across a screen's whole
button set — a lower-priority button can end up with a letter that isn't its
first or most obvious choice (e.g. "Cancel" losing its first two candidate
letters to a higher-priority button and settling for a letter further into
the word) but the result is always collision-free and deterministic for a
given (label, priority order, reserved set).
"""

from __future__ import annotations

from textual.markup import escape

from .command_nav import RESERVED_CTRL_LETTERS


def assign_mnemonic(label: str, used: set[str]) -> tuple[str, str]:
    """Pick a ctrl+letter mnemonic for ``label``.

    Args:
        label: The button's plain-text label (no markup).
        used: Lowercase letters already claimed on this screen — by an
            earlier (higher-priority) call to this function, and/or seeded by
            the caller with letters the hosting screen already binds itself
            (e.g. ``{"s", "e"}`` for a screen with its own ``ctrl+s``/
            ``ctrl+e``). Mutated in place: the chosen letter is added before
            returning, so threading the same set through one call per button
            (in priority order) keeps every button on a screen collision-free.

    Returns:
        A ``(binding_key, markup_label)`` pair: ``binding_key`` is e.g.
        ``"ctrl+r"``, and ``markup_label`` is ``label`` with the chosen
        letter wrapped in ``[underline]...[/underline]`` Textual markup —
        pass it straight to ``Button(markup_label, ...)``.

    Raises:
        ValueError: if every letter in ``label`` is already reserved/used —
            this means the screen has more competing initials than spare
            letters, which should be caught in review/tests rather than
            silently mis-bound at runtime.
    """
    for index, char in enumerate(label):
        lower = char.lower()
        if char.isalpha() and lower not in RESERVED_CTRL_LETTERS and lower not in used:
            used.add(lower)
            markup = (
                escape(label[:index])
                + "[underline]"
                + escape(char)
                + "[/underline]"
                + escape(label[index + 1 :])
            )
            return f"ctrl+{lower}", markup
    raise ValueError(
        f"No available ctrl+letter mnemonic for label {label!r} "
        f"(reserved={sorted(RESERVED_CTRL_LETTERS)}, used={sorted(used)})"
    )
