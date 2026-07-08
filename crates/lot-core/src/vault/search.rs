//! Depth-first searches over the tree of Things, shared by the vault's
//! lookup and archive operations.

use crate::error::Result;
use crate::thing::Thing;
use crate::update::UpdateTypes;
use std::path::PathBuf;

/// Depth-first search for a thing whose id equals `target`, descending into
/// each thing's children.
pub(super) fn find_in(things: Vec<Thing>, target: &str) -> Option<Thing> {
    for thing in things {
        if thing.id().ok().as_deref() == Some(target) {
            return Some(thing);
        }
        if let Ok(children) = thing.children() {
            if let Some(found) = find_in(children, target) {
                return Some(found);
            }
        }
    }
    None
}

/// Depth-first walk collecting every thing whose status is terminal, without
/// descending into the collected things: their subtrees are archived with
/// them, so a nested terminal thing is already covered by its ancestor.
pub(super) fn collect_terminal(
    things: Vec<Thing>,
    types: &UpdateTypes,
    out: &mut Vec<Thing>,
) -> Result<()> {
    for thing in things {
        if types.status_is_terminal(&thing.status()?) {
            out.push(thing);
        } else {
            collect_terminal(thing.children()?, types, out)?;
        }
    }
    Ok(())
}

/// Depth-first walk collecting every *descendant* of `thing` whose status is
/// not terminal — the "active" (not-done) work that archiving `thing` would
/// delete along with it. `thing` itself is never included (naming a thing to
/// archive is explicit consent for that thing; its buried not-done children are
/// the surprise). Each entry is rendered `"<title> (<id>)"` for a legible
/// message. Descends the whole subtree, including under nested terminal things,
/// since archiving removes the folder wholesale.
pub(super) fn collect_active_descendants(
    thing: &Thing,
    types: &UpdateTypes,
    out: &mut Vec<String>,
) -> Result<()> {
    for child in thing.children()? {
        if !types.status_is_terminal(&child.status()?) {
            out.push(format!("{} ({})", child.title()?, child.id()?));
        }
        collect_active_descendants(&child, types, out)?;
    }
    Ok(())
}

/// Depth-first search for the path of the update file whose `update-id` equals
/// `target`, descending into each thing's children.
pub(super) fn find_update_in(things: Vec<Thing>, target: &str) -> Option<PathBuf> {
    for thing in things {
        if let Ok(Some(path)) = thing.update_path_by_id(target) {
            return Some(path);
        }
        if let Ok(children) = thing.children() {
            if let Some(found) = find_update_in(children, target) {
                return Some(found);
            }
        }
    }
    None
}
