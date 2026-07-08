"""Direct unit tests for the pure in-memory vault index.

:class:`VaultIndex` has no Textual dependency, so these tests exercise it as a
plain object — no app shell, pilot, or fake CLI. The app-level behaviour built
on top of it (watch-event application, selection cascades, mark pruning) is
covered in ``test_watch.py`` / ``test_app.py`` / ``test_batch.py``.
"""

from __future__ import annotations

from lot_textual_ui.index import VAULT_ROOT, VaultIndex
from lot_textual_ui.models import Thing


def sample() -> list[Thing]:
    grandchild = Thing(id="g1", name="Grandchild", status="note")
    child = Thing(id="c1", name="Child", status="work", children=[grandchild])
    leaf = Thing(id="c2", name="Leaf", status="note")
    root = Thing(id="r1", name="Root", status="work", children=[child, leaf])
    other = Thing(id="r2", name="Other", status="note")
    return [root, other]


def make_index() -> VaultIndex:
    index = VaultIndex()
    index.reindex(sample())
    return index


# --- reindex -----------------------------------------------------------------


def test_reindex_builds_all_three_maps() -> None:
    index = make_index()
    assert set(index.by_id) == {"r1", "r2", "c1", "c2", "g1"}
    assert [t.id for t in index.roots] == ["r1", "r2"]
    assert index.parent_of["r1"] is None
    assert index.parent_of["c1"].id == "r1"
    assert index.parent_of["g1"].id == "c1"


def test_reindex_replaces_previous_state_wholesale() -> None:
    index = make_index()
    index.reindex([Thing(id="x1", name="X", status="note")])
    assert set(index.by_id) == {"x1"}
    assert [t.id for t in index.roots] == ["x1"]
    assert index.parent_of == {"x1": None}


# --- upsert --------------------------------------------------------------------


def test_upsert_creates_a_new_node_under_its_parent_name_sorted() -> None:
    index = make_index()
    index.upsert_node("c0", "Aardvark", "note", "r1")
    assert index.by_id["c0"].name == "Aardvark"
    assert index.parent_of["c0"].id == "r1"
    # Siblings are kept name-sorted: Aardvark < Child < Leaf.
    assert [t.name for t in index.by_id["r1"].children] == [
        "Aardvark",
        "Child",
        "Leaf",
    ]


def test_upsert_creates_a_new_root_when_parent_is_none() -> None:
    index = make_index()
    index.upsert_node("r0", "Aardvark", "note", None)
    # Linking sorts siblings by *name*: Aardvark < Other (r2) < Root (r1).
    assert [t.id for t in index.roots] == ["r0", "r2", "r1"]
    assert index.parent_of["r0"] is None


def test_upsert_updates_in_place_preserving_children() -> None:
    index = make_index()
    index.upsert_node("c1", "Child renamed", "done", "r1")
    node = index.by_id["c1"]
    assert node.name == "Child renamed"
    assert node.status == "done"
    # Its subtree survived the in-place update.
    assert [t.id for t in node.children] == ["g1"]
    assert index.parent_of["g1"].id == "c1"


def test_upsert_relinks_only_when_the_parent_moved() -> None:
    index = make_index()
    # Move c1 (and implicitly its subtree) from r1 to r2.
    index.upsert_node("c1", "Child", "work", "r2")
    assert index.parent_of["c1"].id == "r2"
    assert [t.id for t in index.by_id["r1"].children] == ["c2"]
    assert [t.id for t in index.by_id["r2"].children] == ["c1"]
    # The moved node kept its descendants.
    assert [t.id for t in index.by_id["c1"].children] == ["g1"]


def test_upsert_can_promote_a_child_to_a_root() -> None:
    index = make_index()
    index.upsert_node("c1", "Child", "work", None)
    assert index.parent_of["c1"] is None
    # Name-sorted on link: Child < Other (r2) < Root (r1).
    assert [t.id for t in index.roots] == ["c1", "r2", "r1"]
    assert [t.id for t in index.by_id["r1"].children] == ["c2"]


# --- remove_subtree -------------------------------------------------------------


def test_remove_subtree_drops_descendants_and_returns_their_ids() -> None:
    index = make_index()
    removed = index.remove_subtree("c1")
    assert removed == {"c1", "g1"}
    assert "c1" not in index.by_id
    assert "g1" not in index.by_id
    assert "c1" not in index.parent_of
    assert [t.id for t in index.by_id["r1"].children] == ["c2"]


def test_remove_subtree_of_a_root_updates_the_root_list() -> None:
    index = make_index()
    removed = index.remove_subtree("r1")
    assert removed == {"r1", "c1", "c2", "g1"}
    assert [t.id for t in index.roots] == ["r2"]


def test_remove_subtree_of_an_unknown_id_is_a_no_op() -> None:
    index = make_index()
    assert index.remove_subtree("nope") == set()
    assert set(index.by_id) == {"r1", "r2", "c1", "c2", "g1"}


# --- left_visible_id -------------------------------------------------------------


def test_left_visible_id_keeps_roots_and_branches() -> None:
    index = make_index()
    assert index.left_visible_id("r1") == "r1"  # a root
    assert index.left_visible_id("r2") == "r2"  # a childless root still shows
    assert index.left_visible_id("c1") == "c1"  # a branch


def test_left_visible_id_maps_a_leaf_to_its_parent() -> None:
    index = make_index()
    assert index.left_visible_id("c2") == "r1"
    assert index.left_visible_id("g1") == "c1"


def test_left_visible_id_passes_unknown_ids_through() -> None:
    index = make_index()
    assert index.left_visible_id("nope") == "nope"


# --- resolve_selection ------------------------------------------------------------


def test_resolve_selection_keeps_a_still_present_id() -> None:
    index = make_index()
    assert index.resolve_selection("c1", "r1") == "c1"


def test_resolve_selection_falls_back_to_the_old_parent() -> None:
    index = make_index()
    index.remove_subtree("c1")
    assert index.resolve_selection("c1", "r1") == "r1"


def test_resolve_selection_falls_back_to_the_first_root() -> None:
    index = make_index()
    index.remove_subtree("r1")
    assert index.resolve_selection("c1", "r1") == "r2"


def test_resolve_selection_falls_back_to_the_vault_root_when_empty() -> None:
    index = VaultIndex()
    assert index.resolve_selection("c1", "r1") == VAULT_ROOT


def test_resolve_selection_vault_root_always_survives() -> None:
    index = make_index()
    assert index.resolve_selection(VAULT_ROOT, None) == VAULT_ROOT
