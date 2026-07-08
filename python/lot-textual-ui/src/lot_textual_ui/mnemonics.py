"""Ctrl+letter keyboard mnemonics for modal buttons.

Every :class:`~textual.widgets.Button` in the app's modal forms
(:mod:`lot_textual_ui.forms`, :mod:`lot_textual_ui.batch`) gets a
``ctrl+<letter>`` shortcut derived from its own label, with that letter
underlined in the visible label so the shortcut is discoverable without a
footer hint. :func:`assign_mnemonic` is the one place that picks the letter,
so every screen applies the same rule instead of each hand-picking one.

The rule
--------

For each button, in **assignment order** (every modal screen assigns its
**Cancel** button first — see below — then its primary/confirm action):

1. Walk the label's characters left to right.
2. Skip anything that is not a letter, and anything (case-insensitively)
   already spoken for — either reserved in :data:`_RESERVED_LETTERS` or
   reserved by the *hosting screen itself* (its own ``ctrl+s`` submit,
   ``$EDITOR`` hatch, etc — passed in via ``used``). :data:`_RESERVED_LETTERS`
   is the app-wide navigation set
   (:data:`~lot_textual_ui.command_nav.RESERVED_CTRL_LETTERS`: ``ctrl+c``/
   ``p``/``q``/``z``) *plus* the destructive text-editing chords in
   :data:`_EDITING_CTRL_LETTERS` *plus* the emacs/readline cursor-navigation
   chords in :data:`_NAV_CTRL_LETTERS`.
3. The first letter that clears both checks is the mnemonic; it is recorded
   into ``used`` (mutated in place) so the *next* button's search also avoids
   it.

Why Cancel goes first
---------------------

Callers assign **Cancel before the primary action** on every screen. That
pins Cancel to the same chord (``ctrl+l`` — the first letter of "Cancel" left
once the reserved editing/navigation chords are ruled out) on the New-Thing,
New/Batch-Update and Archive/Delete confirm dialogs, and — because Cancel's
letter is in ``used`` before the primary action picks — guarantees no screen's
submit/confirm chord can ever equal another screen's Cancel chord. A user who
learns "``ctrl+l`` closes this dialog" cannot then fire a destructive Archive
with the same chord elsewhere.

This is a plain greedy pick, not a global optimiser across a screen's whole
button set — a later button can end up with a letter that isn't its first or
most obvious choice (e.g. "Create" past its reserved ``c`` to ``r``) but the
result is always collision-free and deterministic for a given (label,
assignment order, reserved set).
"""

from __future__ import annotations

from textual.markup import escape

from .command_nav import RESERVED_CTRL_LETTERS

# ``ctrl+<letter>`` chords a focused :class:`~textual.widgets.Input` /
# :class:`~textual.widgets.TextArea` binds to *destructive* line-editing
# actions — ``ctrl+w`` (delete word), ``ctrl+u`` (delete to line start),
# ``ctrl+k`` (delete to line end), ``ctrl+x`` (cut), ``ctrl+v`` (paste),
# ``ctrl+y`` (redo) — plus ``ctrl+a``, which Textual's Input does *not* bind
# but which is near-universal muscle memory for cursor-to-line-start /
# select-all. A button mnemonic must never shadow one of these: these forms
# put the cursor in a text field, so a mnemonic that stole ``ctrl+w`` would
# eat "delete word", and (before this reservation) Cancel landing on ``ctrl+a``
# discarded a half-filled form on a chord users reach for mid-edit.
_EDITING_CTRL_LETTERS = frozenset("aukwxvy")

# The emacs/readline *cursor-navigation* chords a focused text field answers to:
# ``ctrl+a``/``ctrl+e`` (line start/end), ``ctrl+b``/``ctrl+f`` (char back/
# forward), ``ctrl+n``/``ctrl+p`` (line down/up). These are pervasive muscle
# memory — a user mid-edit reaches for them without looking — so, like the
# destructive chords above, a button mnemonic must never shadow one. This is
# the direct fix for a reported data-loss trap: Cancel used to land on
# ``ctrl+n``, so pressing it to move the cursor down a line instead dismissed
# the whole form. (``ctrl+a`` also appears in the editing set; ``ctrl+p`` also
# in :data:`~lot_textual_ui.command_nav.RESERVED_CTRL_LETTERS` — the union
# below dedups the overlaps.)
_NAV_CTRL_LETTERS = frozenset("aebfnp")

# Kept **separate** from :data:`~lot_textual_ui.command_nav.RESERVED_CTRL_LETTERS`
# on purpose: that set is the app-wide *navigation* reservation and
# ``commands.py`` also consumes it to gate command-navigator entry
# (``ctrl+<letter>`` into a top-level command). The editing and cursor-nav
# chords are a mnemonics-only concern, so reserving them here leaves the
# navigator's shortcut set — and ``command_nav`` — untouched.
#
# Every letter :func:`assign_mnemonic` refuses: the app-wide navigation
# reservations plus the text-editing and cursor-navigation chords above.
_RESERVED_LETTERS = RESERVED_CTRL_LETTERS | _EDITING_CTRL_LETTERS | _NAV_CTRL_LETTERS


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
        if char.isalpha() and lower not in _RESERVED_LETTERS and lower not in used:
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
        f"(reserved={sorted(_RESERVED_LETTERS)}, used={sorted(used)})"
    )
