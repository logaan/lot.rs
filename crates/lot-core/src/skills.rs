use crate::error::{io_err, Error, Result};
use std::path::{Path, PathBuf};

/// The bundled `lot-task` skill, embedded at compile time.
pub const LOT_TASK_SKILL: &str = include_str!("../../../data/skills/lot-task/SKILL.md");

/// The name of the task skill, also used as the slash-command name.
pub const LOT_TASK_SKILL_NAME: &str = "lot-task";

/// A bundled **coordinator** skill: the one a `lot claude coordinate` session
/// loads to drive a root Thing's subtree of child Things.
///
/// `alias` is the short selector on the command line (`lot claude coordinate
/// <model> <alias> [id]`); `name` is both the installed skill directory and the
/// slash-command the session is launched with (`/<name> <id>`); `contents` is
/// the embedded `SKILL.md` written by [`install`].
pub struct CoordinateSkill {
    /// The `lot claude coordinate <model> <alias>` selector (e.g. `plan`).
    pub alias: &'static str,
    /// The installed skill / slash-command name (e.g. `lot-coordinate-plan`).
    pub name: &'static str,
    /// The embedded `SKILL.md` contents.
    pub contents: &'static str,
}

/// The bundled coordinator skills, one per workflow (see each `SKILL.md`):
/// `decide` (Decide, Plan, Initiate), `plan` (Plan, Act), and `act` (Act with
/// an existing plan). The user picks one by alias when starting a coordinator
/// with `lot claude coordinate`.
pub const COORDINATE_SKILLS: &[CoordinateSkill] = &[
    CoordinateSkill {
        alias: "decide",
        name: "lot-coordinate-decide",
        contents: include_str!("../../../data/skills/lot-coordinate-decide/SKILL.md"),
    },
    CoordinateSkill {
        alias: "plan",
        name: "lot-coordinate-plan",
        contents: include_str!("../../../data/skills/lot-coordinate-plan/SKILL.md"),
    },
    CoordinateSkill {
        alias: "act",
        name: "lot-coordinate-act",
        contents: include_str!("../../../data/skills/lot-coordinate-act/SKILL.md"),
    },
];

/// Resolve a `lot claude coordinate` skill alias (e.g. `plan`) to its installed
/// skill / slash-command name (e.g. `lot-coordinate-plan`), or `None` when the
/// alias names no bundled coordinator skill.
pub fn coordinate_skill_name(alias: &str) -> Option<&'static str> {
    COORDINATE_SKILLS
        .iter()
        .find(|s| s.alias == alias)
        .map(|s| s.name)
}

/// The available coordinator skill aliases, comma-joined for help/error text.
pub fn coordinate_aliases() -> String {
    COORDINATE_SKILLS
        .iter()
        .map(|s| s.alias)
        .collect::<Vec<_>>()
        .join(", ")
}

/// The directory where user-level Claude skills are installed
/// (`~/.claude/skills`).
pub fn skills_dir() -> Result<PathBuf> {
    let home = std::env::var_os("HOME").ok_or(Error::NoConfigDir)?;
    Ok(PathBuf::from(home).join(".claude").join("skills"))
}

/// Install the bundled LoT skills into the user's `~/.claude/skills` directory:
/// the `lot-task` worker skill and every coordinator skill in
/// [`COORDINATE_SKILLS`]. Returns the paths that were written, in install order.
pub fn install() -> Result<Vec<PathBuf>> {
    install_into(&skills_dir()?)
}

/// Write every bundled skill under `base` (a `.../skills` directory), one
/// `<base>/<name>/SKILL.md` per skill. Split from [`install`] so it can be
/// tested against a temp directory without touching `HOME`.
fn install_into(base: &Path) -> Result<Vec<PathBuf>> {
    // The worker skill first, then every coordinator skill.
    let mut skills: Vec<(&str, &str)> = vec![(LOT_TASK_SKILL_NAME, LOT_TASK_SKILL)];
    skills.extend(COORDINATE_SKILLS.iter().map(|s| (s.name, s.contents)));

    let mut written = Vec::with_capacity(skills.len());
    for (name, contents) in skills {
        let dir = base.join(name);
        std::fs::create_dir_all(&dir).map_err(io_err(&dir))?;
        let skill_path = dir.join("SKILL.md");
        std::fs::write(&skill_path, contents).map_err(io_err(&skill_path))?;
        written.push(skill_path);
    }
    Ok(written)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn coordinate_aliases_resolve_to_skill_names() {
        assert_eq!(
            coordinate_skill_name("decide"),
            Some("lot-coordinate-decide")
        );
        assert_eq!(coordinate_skill_name("plan"), Some("lot-coordinate-plan"));
        assert_eq!(coordinate_skill_name("act"), Some("lot-coordinate-act"));
        // An unknown alias resolves to nothing.
        assert_eq!(coordinate_skill_name("bogus"), None);
        // The joined alias list is what help/error text shows.
        assert_eq!(coordinate_aliases(), "decide, plan, act");
    }

    #[test]
    fn each_coordinate_skill_dir_matches_its_frontmatter_name() {
        // The installed directory name must equal the skill's `name:`
        // frontmatter, or Claude won't find the slash-command.
        for skill in COORDINATE_SKILLS {
            let needle = format!("name: {}", skill.name);
            assert!(
                skill.contents.contains(&needle),
                "{} SKILL.md is missing `{needle}`",
                skill.name
            );
        }
    }

    #[test]
    fn install_writes_the_worker_and_every_coordinator_skill() {
        // Install into a temp dir (no HOME mutation, so this can't race other
        // tests): every bundled skill lands and its path is returned.
        let dir = tempfile::tempdir().unwrap();
        let base = dir.path();

        let written = install_into(base).unwrap();
        // lot-task plus the three coordinator skills.
        assert_eq!(written.len(), 1 + COORDINATE_SKILLS.len());

        for name in
            std::iter::once(LOT_TASK_SKILL_NAME).chain(COORDINATE_SKILLS.iter().map(|s| s.name))
        {
            let path = base.join(name).join("SKILL.md");
            assert!(path.is_file(), "{name} SKILL.md was not written");
            assert!(written.contains(&path), "{name} path not returned");
        }
    }
}
