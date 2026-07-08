"""Typed models for the data returned by the ``lot`` CLI.

These dataclasses are parsed straight out of the YAML that ``lot`` prints. They
are deliberately tolerant: the CLI may grow new frontmatter keys, so anything
not mapped onto a named field is preserved in an ``extra`` mapping rather than
being dropped.

Only :mod:`lot_textual_ui.lot_cli` should construct these from real CLI output;
the rest of the UI consumes the models.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


def _clean(mapping: object) -> dict[str, Any]:
    """Return a shallow ``dict`` copy of a parsed-YAML mapping.

    YAML nulls (empty documents) parse to ``None``; treat those as empty.
    """
    if mapping is None:
        return {}
    if not isinstance(mapping, dict):
        raise TypeError(f"expected a YAML mapping, got {type(mapping).__name__}")
    return dict(mapping)


@dataclass
class Thing:
    """A node in the Thing tree, as printed by ``lot thing list``.

    ``lot thing list`` already returns the full nested hierarchy, so
    :attr:`children` is populated recursively straight out of the YAML.
    :attr:`updated` is the Thing's most-recent-update timestamp (the CLI's
    ``updated`` key), or ``None`` when the Thing has no timestamped update; it
    backs the UI's recency sort and is absent from the YAML for a Thing that has
    never been updated.
    """

    id: str
    name: str
    status: str
    children: list[Thing] = field(default_factory=list)
    updated: str | None = None

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Thing:
        raw = _clean(data)
        children = [cls.from_dict(child) for child in raw.get("children") or []]
        return cls(
            id=raw.get("id", ""),
            name=raw.get("name", ""),
            status=raw.get("status", ""),
            children=children,
            updated=raw.get("updated"),
        )


@dataclass
class ThingList:
    """The result of ``lot thing list``: a vault path and its top-level Things."""

    path: str
    things: list[Thing] = field(default_factory=list)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ThingList:
        raw = _clean(data)
        things = [Thing.from_dict(t) for t in raw.get("things") or []]
        return cls(path=raw.get("path", ""), things=things)


@dataclass
class ComputedState:
    """The computed current state of a Thing, from ``lot thing get``.

    The named fields cover what the CLI reliably emits; every ``*-at``
    timestamp the CLI includes (``note-at``, ``work-at``, ``info-at``,
    ``done-at``, ``created-at``, …) is collected into :attr:`timestamps`, and
    any other unrecognised key is kept in :attr:`extra` so nothing is lost.
    """

    status: str
    task_id: str
    update_id: str
    body: str | None = None
    timestamps: dict[str, str] = field(default_factory=dict)
    extra: dict[str, Any] = field(default_factory=dict)

    _NAMED = {"status", "task-id", "update-id", "body"}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ComputedState:
        raw = _clean(data)
        timestamps = {key: value for key, value in raw.items() if key.endswith("-at")}
        extra = {
            key: value
            for key, value in raw.items()
            if key not in cls._NAMED and not key.endswith("-at")
        }
        return cls(
            status=raw.get("status", ""),
            task_id=raw.get("task-id", ""),
            update_id=raw.get("update-id", ""),
            body=raw.get("body"),
            timestamps=timestamps,
            extra=extra,
        )


@dataclass
class Update:
    """A single entry from a Thing's update thread (``lot thing updates``).

    Update types are vault-configured (stock set: ``note``/``work``/``info``/
    ``done``), so :attr:`type` is a plain string and any type parses fine. Any
    frontmatter beyond the named fields (e.g. the ``task-id`` carried by a
    Thing's first update) is preserved in :attr:`extra`.
    """

    update_id: str
    type: str
    at: str | None = None
    body: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    _NAMED = {"update-id", "type", "at", "body"}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Update:
        raw = _clean(data)
        extra = {key: value for key, value in raw.items() if key not in cls._NAMED}
        return cls(
            update_id=raw.get("update-id", ""),
            type=raw.get("type", ""),
            at=raw.get("at"),
            body=raw.get("body"),
            extra=extra,
        )


@dataclass
class UpdateType:
    """One effective update type, from ``lot settings get``'s ``update-types``.

    Update types are entirely vault-configured — ``lot`` has no built-in
    types — and ``lot settings get`` always lists the full effective set
    (readme §1.3, §5.5.1). Each entry carries the flags a front-end needs to
    offer the type without understanding config files:

    * :attr:`takes_body` — the type carries a markdown body (like the stock
      ``work``); ``False`` makes it a bare marker (like the stock ``done``).
    * :attr:`terminal` — an update of this type retires the Thing's status.
    """

    name: str
    takes_body: bool = True
    terminal: bool = False

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> UpdateType:
        raw = _clean(data)
        return cls(
            name=str(raw.get("name", "")),
            takes_body=bool(raw.get("takes-body", True)),
            terminal=bool(raw.get("terminal", False)),
        )


@dataclass
class VaultEntry:
    """One configured vault, from the ``vaults`` list of ``lot settings get``.

    ``path`` is always present; ``name`` is the optional human label a vault may
    carry in config (``None`` when unnamed). The vault-switching work item reuses
    this to populate its picker, so keep both fields.
    """

    path: str
    name: str | None = None

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> VaultEntry:
        raw = _clean(data)
        return cls(path=str(raw.get("path", "")), name=raw.get("name"))


@dataclass
class EffectiveConfig:
    """The merged effective config from ``lot settings get`` (readme §5.5).

    ``lot settings get`` emits the merged user+vault config with every key always
    present, so this model mirrors that shape exactly:

    * :attr:`theme` — the configured Textual theme name, or ``None`` when unset.
    * :attr:`keybindings` — an ``action -> key`` map (``{}`` when none); the
      keybinding-override work item consumes this.
    * :attr:`vaults` — the configured vaults as :class:`VaultEntry`\\ s (``[]``
      when none); the vault-switching work item consumes this.
    * :attr:`vault_path` — the resolved active vault path (the CLI emits it under
      the ``vault-path`` key).
    * :attr:`update_types` — the full effective set of update types (entirely
      vault-configured, readme §1.3) as :class:`UpdateType`\\ s. ``[]`` when
      config defines none — there is no fallback set (``lot`` seeds the stock
      types into new vault configs only), so the app warns and the update
      forms have nothing to offer until types are configured.
    * :attr:`default_update_type` — the name of the type ``lot thing new``
      writes as a Thing's first update (the CLI emits it under the
      ``default-update-type`` key; ``None`` when unset — no fallback name).

    The full shape is parsed here — not just the theme this work item needs — so
    the downstream keybinding and vault agents can reuse ``config_get`` and this
    model unchanged.
    """

    theme: str | None = None
    keybindings: dict[str, str] = field(default_factory=dict)
    vaults: list[VaultEntry] = field(default_factory=list)
    vault_path: str = ""
    update_types: list[UpdateType] = field(default_factory=list)
    default_update_type: str | None = None

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> EffectiveConfig:
        raw = _clean(data)
        keybindings_raw = raw.get("keybindings") or {}
        keybindings = {str(k): str(v) for k, v in dict(keybindings_raw).items()}
        vaults = [VaultEntry.from_dict(v) for v in raw.get("vaults") or []]
        update_types = [UpdateType.from_dict(t) for t in raw.get("update-types") or []]
        default_update_type = raw.get("default-update-type")
        return cls(
            theme=raw.get("theme"),
            keybindings=keybindings,
            vaults=vaults,
            vault_path=str(raw.get("vault-path", "")),
            update_types=update_types,
            default_update_type=(
                str(default_update_type) if default_update_type is not None else None
            ),
        )


@dataclass
class WatchEvent:
    """One minimal, incremental event from the ``lot watch`` stream.

    An event carries only enough to patch a single node of a consumer's tree
    index — never a whole-vault snapshot. Its shape depends on :attr:`kind`:

    * ``created`` / ``modified``: :attr:`id`, :attr:`name`, :attr:`status` and
      :attr:`parent` (the fields to upsert one node; ``parent`` is ``None`` for a
      top-level Thing), plus :attr:`state` and :attr:`updates` — mirroring
      ``lot thing get`` / ``lot thing updates`` so a detail view of the changed
      Thing needs no follow-up ``lot`` call.
    * ``deleted``: :attr:`id` only; the consumer drops that id and its
      descendants.
    * ``reload``: nothing but :attr:`kind`; the rare fallback for a batch that
      maps to no single Thing, telling the consumer to reload its baseline.

    Everything after :attr:`kind` is optional so unrelated fields are simply
    absent, and the named parsers are reused so a ``WatchEvent`` carries exactly
    the same typed models the individual read commands return.
    """

    kind: str
    id: str | None = None
    name: str | None = None
    status: str | None = None
    parent: str | None = None
    state: ComputedState | None = None
    updates: list[Update] | None = None

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> WatchEvent:
        raw = _clean(data)
        state_raw = raw.get("state")
        state = ComputedState.from_dict(state_raw) if state_raw is not None else None
        updates_raw = raw.get("updates")
        updates = (
            [Update.from_dict(entry) for entry in updates_raw]
            if updates_raw is not None
            else None
        )
        return cls(
            kind=raw.get("kind", ""),
            id=raw.get("id"),
            name=raw.get("name"),
            status=raw.get("status"),
            parent=raw.get("parent"),
            state=state,
            updates=updates,
        )
