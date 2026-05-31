use crate::error::{io_err, Error, Result};
use std::path::PathBuf;

/// The bundled `lot-task` skill, embedded at compile time.
pub const LOT_TASK_SKILL: &str = include_str!("../../../data/skills/lot-task/SKILL.md");

/// The name of the task skill, also used as the slash-command name.
pub const LOT_TASK_SKILL_NAME: &str = "lot-task";

/// The directory where user-level Claude skills are installed
/// (`~/.claude/skills`).
pub fn skills_dir() -> Result<PathBuf> {
    let home = std::env::var_os("HOME").ok_or(Error::NoConfigDir)?;
    Ok(PathBuf::from(home).join(".claude").join("skills"))
}

/// Install the bundled LoT skills into the user's `~/.claude/skills` directory.
/// Returns the paths that were written.
pub fn install() -> Result<Vec<PathBuf>> {
    let dir = skills_dir()?.join(LOT_TASK_SKILL_NAME);
    std::fs::create_dir_all(&dir).map_err(io_err(&dir))?;
    let skill_path = dir.join("SKILL.md");
    std::fs::write(&skill_path, LOT_TASK_SKILL).map_err(io_err(&skill_path))?;
    Ok(vec![skill_path])
}
