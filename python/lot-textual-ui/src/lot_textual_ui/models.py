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
    """

    id: str
    name: str
    status: str
    children: list[Thing] = field(default_factory=list)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Thing:
        raw = _clean(data)
        children = [cls.from_dict(child) for child in raw.get("children") or []]
        return cls(
            id=raw.get("id", ""),
            name=raw.get("name", ""),
            status=raw.get("status", ""),
            children=children,
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

    Known types today are ``created``, ``note``, ``work``, ``info`` and
    ``done``, but :attr:`type` is a plain string so new kinds parse fine. Any
    frontmatter beyond the named fields (e.g. the ``task-id`` carried by the
    first ``note``) is preserved in :attr:`extra`.
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
