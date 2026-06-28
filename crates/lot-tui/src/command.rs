//! The command palette: navigating the `lot` command tree by typing letters.
//!
//! The tree is discovered at startup from `lot help --format=yaml` (parsed into
//! [`CommandNode`]), so the palette reflects whatever `lot` is installed rather
//! than a hard-coded list. [`Palette`] is the live navigation state; it is a
//! pure state machine (it never touches the terminal or filesystem) so the
//! event loop can drive it and it can be unit-tested.

use ratatui::crossterm::event::{KeyCode, KeyEvent};
use serde::Deserialize;
use std::time::{Duration, Instant};

/// How long after a chooser appears the confirming <kbd>Enter</kbd> is ignored,
/// so a stray Enter can't accidentally pick an option the user didn't see.
pub const CHOOSER_GUARD: Duration = Duration::from_millis(250);

/// One command (or sub-command) in the tree. Only the fields the palette needs
/// are kept; the rest of the `lot help --format=yaml` document is ignored.
#[derive(Debug, Clone, Default, Deserialize)]
pub struct CommandNode {
    pub name: String,
    #[serde(default)]
    pub about: Option<String>,
    #[serde(default)]
    pub subcommands: Vec<CommandNode>,
}

impl CommandNode {
    /// Parse the YAML emitted by `lot help --format=yaml`.
    pub fn parse(yaml: &str) -> serde_yaml_ng::Result<CommandNode> {
        serde_yaml_ng::from_str(yaml)
    }
}

/// What the event loop should do after the palette handled a key.
#[derive(Debug, PartialEq, Eq)]
pub enum Outcome {
    /// Nothing for the loop to do; the palette stays open (state may have moved).
    None,
    /// Close the palette.
    Close,
    /// Run `lot` with these sub-command args, then reload.
    Invoke(Vec<String>),
    /// Open the `?` shortcut-tree overlay (the palette stays open beneath it).
    OpenHelp,
}

/// A disambiguation list shown when a typed letter matches several commands.
#[derive(Debug, Clone)]
pub struct Chooser {
    /// Indices (into the current level) of the colliding commands.
    pub candidates: Vec<usize>,
    /// Which candidate is highlighted.
    pub selected: usize,
    /// When the list appeared, for the [`CHOOSER_GUARD`].
    pub opened_at: Instant,
}

/// Live palette navigation state: where we are in the tree and any open chooser.
#[derive(Debug, Clone)]
pub struct Palette {
    /// Indices from the root to the current level (empty = at the top level).
    pub path: Vec<usize>,
    /// The open disambiguation list, if any.
    pub chooser: Option<Chooser>,
}

impl Palette {
    /// A fresh palette at the top level.
    pub fn new() -> Palette {
        Palette {
            path: Vec::new(),
            chooser: None,
        }
    }

    /// Handle a key against the command `root`. `now` is the current instant
    /// (injected so the chooser guard is testable).
    pub fn on_key(&mut self, key: KeyEvent, root: &CommandNode, now: Instant) -> Outcome {
        if self.chooser.is_some() {
            return self.on_chooser_key(key, root, now);
        }
        match key.code {
            // Clear all navigation input; close when already at the top.
            KeyCode::Esc => {
                if self.path.is_empty() {
                    Outcome::Close
                } else {
                    self.path.clear();
                    Outcome::None
                }
            }
            // Undo the most recent step; close when there's nothing to undo.
            KeyCode::Backspace => {
                if self.path.is_empty() {
                    Outcome::Close
                } else {
                    self.path.pop();
                    Outcome::None
                }
            }
            // Invoke the current node (a no-op at the bare top level).
            KeyCode::Enter => {
                if self.path.is_empty() {
                    Outcome::None
                } else {
                    Outcome::Invoke(self.command_args(root))
                }
            }
            KeyCode::Char('?') => Outcome::OpenHelp,
            KeyCode::Char(c) if !c.is_whitespace() => self.on_letter(c, root, now),
            _ => Outcome::None,
        }
    }

    /// Handle a key while a chooser is open.
    fn on_chooser_key(&mut self, key: KeyEvent, root: &CommandNode, now: Instant) -> Outcome {
        match key.code {
            KeyCode::Up | KeyCode::Char('k') => self.move_chooser(-1),
            KeyCode::Down | KeyCode::Char('j') => self.move_chooser(1),
            // Confirming a pick navigates into it; a leaf runs straight away.
            KeyCode::Enter => {
                if self.confirm_chooser(now) {
                    return self.invoke_if_leaf(root);
                }
            }
            // Dismiss the chooser, back to navigation.
            KeyCode::Esc | KeyCode::Backspace => self.chooser = None,
            _ => {}
        }
        Outcome::None
    }

    /// Type a letter at the current level: navigate to the unique match, open a
    /// chooser when several match, or do nothing when none do. A unique match
    /// that is a leaf (no sub-commands) is invoked straight away.
    fn on_letter(&mut self, c: char, root: &CommandNode, now: Instant) -> Outcome {
        let lc = c.to_ascii_lowercase();
        let matches: Vec<usize> = self
            .current_children(root)
            .iter()
            .enumerate()
            .filter(|(_, n)| n.name.chars().next().map(lower) == Some(lc))
            .map(|(i, _)| i)
            .collect();
        match matches.as_slice() {
            [] => Outcome::None,
            [only] => {
                self.path.push(*only);
                self.invoke_if_leaf(root)
            }
            _ => {
                self.chooser = Some(Chooser {
                    candidates: matches,
                    selected: 0,
                    opened_at: now,
                });
                Outcome::None
            }
        }
    }

    /// Invoke the node at the current path when it is a leaf (no sub-commands),
    /// so navigating onto a runnable command fires it without a separate
    /// <kbd>Enter</kbd>; otherwise stay put and keep navigating.
    fn invoke_if_leaf(&self, root: &CommandNode) -> Outcome {
        if !self.path.is_empty() && self.current_children(root).is_empty() {
            Outcome::Invoke(self.command_args(root))
        } else {
            Outcome::None
        }
    }

    /// Move the chooser highlight, clamped to its candidates.
    fn move_chooser(&mut self, delta: isize) {
        if let Some(ch) = &mut self.chooser {
            let last = ch.candidates.len().saturating_sub(1) as isize;
            ch.selected = (ch.selected as isize + delta).clamp(0, last) as usize;
        }
    }

    /// Confirm the highlighted chooser candidate, but only once the guard has
    /// elapsed. Returns whether it confirmed.
    pub fn confirm_chooser(&mut self, now: Instant) -> bool {
        let Some(ch) = &self.chooser else {
            return false;
        };
        if now.duration_since(ch.opened_at) < CHOOSER_GUARD {
            return false;
        }
        let chosen = ch.candidates[ch.selected];
        self.path.push(chosen);
        self.chooser = None;
        true
    }

    /// The commands at the current level (follows `path`; empty on a stale path).
    pub fn current_children<'a>(&self, root: &'a CommandNode) -> &'a [CommandNode] {
        let mut node = root;
        for &i in &self.path {
            match node.subcommands.get(i) {
                Some(child) => node = child,
                None => return &[],
            }
        }
        &node.subcommands
    }

    /// The sub-command names along `path`, i.e. the args to pass to `lot`.
    pub fn command_args(&self, root: &CommandNode) -> Vec<String> {
        let mut args = Vec::new();
        let mut node = root;
        for &i in &self.path {
            let Some(child) = node.subcommands.get(i) else {
                break;
            };
            args.push(child.name.clone());
            node = child;
        }
        args
    }

    /// A human-readable breadcrumb of the current position, e.g. `lot thing`.
    pub fn breadcrumb(&self, root: &CommandNode) -> String {
        let mut crumb = root.name.clone();
        for name in self.command_args(root) {
            crumb.push(' ');
            crumb.push_str(&name);
        }
        crumb
    }
}

impl Default for Palette {
    fn default() -> Self {
        Palette::new()
    }
}

/// Lower-case the first character of a name for case-insensitive matching.
fn lower(c: char) -> char {
    c.to_ascii_lowercase()
}

#[cfg(test)]
mod tests {
    use super::*;

    /// A small tree for exercising the Palette. It roughly follows the real `lot`
    /// shape (`vault`/`thing`/`update`/`interface`) but is deliberately *not* a
    /// copy of it: the real tree has no first-letter collisions (since `ui`
    /// became `interface`), so we add a synthetic `undo` leaf alongside `update`
    /// to keep a `u` collision for the chooser tests.
    fn tree() -> CommandNode {
        fn leaf(name: &str) -> CommandNode {
            CommandNode {
                name: name.into(),
                about: None,
                subcommands: Vec::new(),
            }
        }
        fn branch(name: &str, kids: Vec<CommandNode>) -> CommandNode {
            CommandNode {
                name: name.into(),
                about: None,
                subcommands: kids,
            }
        }
        branch(
            "lot",
            vec![
                branch("vault", vec![leaf("new")]),
                branch("thing", vec![leaf("new"), leaf("get"), leaf("list")]),
                branch("update", vec![leaf("work"), leaf("info"), leaf("done")]),
                leaf("undo"),
                leaf("interface"),
            ],
        )
    }

    fn key(c: char) -> KeyEvent {
        KeyEvent::from(KeyCode::Char(c))
    }

    fn press(code: KeyCode) -> KeyEvent {
        KeyEvent::from(code)
    }

    #[test]
    fn unique_letter_navigates_down() {
        let root = tree();
        let mut p = Palette::new();
        // `v` is unique at the top level -> straight into vault.
        assert_eq!(p.on_key(key('v'), &root, Instant::now()), Outcome::None);
        assert_eq!(p.command_args(&root), vec!["vault"]);
        assert!(p.chooser.is_none());
    }

    #[test]
    fn parse_reads_yaml_tree() {
        let yaml = "name: lot\nsubcommands:\n- name: thing\n  subcommands:\n  - name: new\n";
        let root = CommandNode::parse(yaml).unwrap();
        assert_eq!(root.name, "lot");
        assert_eq!(root.subcommands[0].name, "thing");
        assert_eq!(root.subcommands[0].subcommands[0].name, "new");
    }

    #[test]
    fn ambiguous_letter_opens_chooser_then_selects() {
        let root = tree();
        let mut p = Palette::new();
        // `u` matches both `update` and `undo`.
        p.on_key(key('u'), &root, Instant::now());
        let ch = p.chooser.as_ref().expect("a chooser opened");
        assert_eq!(ch.candidates.len(), 2);
        assert_eq!(ch.selected, 0);

        // Move to `undo` and confirm (past the guard); `undo` is a leaf so the
        // confirming Enter both picks it and runs it.
        let opened = ch.opened_at;
        p.on_chooser_key(press(KeyCode::Down), &root, opened);
        assert_eq!(
            p.on_chooser_key(press(KeyCode::Enter), &root, opened + CHOOSER_GUARD),
            Outcome::Invoke(vec!["undo".into()])
        );
        assert!(p.chooser.is_none());
        assert_eq!(p.command_args(&root), vec!["undo"]);
    }

    #[test]
    fn chooser_enter_ignored_within_guard() {
        let root = tree();
        let mut p = Palette::new();
        p.on_key(key('u'), &root, Instant::now());
        let opened = p.chooser.as_ref().unwrap().opened_at;

        // Enter before the guard elapses does nothing.
        assert!(!p.confirm_chooser(opened + Duration::from_millis(100)));
        assert!(p.chooser.is_some());
        // Enter after the guard confirms the (default) first candidate, `update`.
        assert!(p.confirm_chooser(opened + CHOOSER_GUARD));
        assert_eq!(p.command_args(&root), vec!["update"]);
    }

    #[test]
    fn backspace_undoes_and_escape_clears() {
        let root = tree();
        let mut p = Palette::new();
        // Walk to `thing new` (`t` is now a unique match -> straight in).
        p.on_key(key('t'), &root, Instant::now());
        p.on_key(key('n'), &root, Instant::now());
        assert_eq!(p.command_args(&root), vec!["thing", "new"]);

        // Backspace pops one level.
        assert_eq!(
            p.on_key(press(KeyCode::Backspace), &root, Instant::now()),
            Outcome::None
        );
        assert_eq!(p.command_args(&root), vec!["thing"]);

        // Escape clears all the way back to the top.
        p.on_key(key('g'), &root, Instant::now()); // into thing/get
        assert_eq!(
            p.on_key(press(KeyCode::Esc), &root, Instant::now()),
            Outcome::None
        );
        assert!(p.path.is_empty());
        // Escape again at the top closes the palette.
        assert_eq!(
            p.on_key(press(KeyCode::Esc), &root, Instant::now()),
            Outcome::Close
        );
    }

    #[test]
    fn enter_invokes_current_node() {
        let root = tree();
        let mut p = Palette::new();
        // `vault` is a branch, so navigating onto it doesn't auto-invoke; an
        // explicit Enter still invokes whatever node we're parked on.
        p.on_key(key('v'), &root, Instant::now()); // vault
        assert_eq!(
            p.on_key(press(KeyCode::Enter), &root, Instant::now()),
            Outcome::Invoke(vec!["vault".into()])
        );
    }

    #[test]
    fn unique_letter_on_leaf_invokes_immediately() {
        let root = tree();
        let mut p = Palette::new();
        // `t` -> `thing` is a branch, so we just navigate into it.
        assert_eq!(p.on_key(key('t'), &root, Instant::now()), Outcome::None);
        // `n` -> `thing new` is a leaf, so it runs without a separate Enter.
        assert_eq!(
            p.on_key(key('n'), &root, Instant::now()),
            Outcome::Invoke(vec!["thing".into(), "new".into()])
        );
    }

    #[test]
    fn chooser_pick_of_branch_navigates_then_leaf_letter_invokes() {
        let root = tree();
        let mut p = Palette::new();
        // `u` collides between `update` and `undo`; the default pick is `update`,
        // a branch, so confirming it only navigates.
        p.on_key(key('u'), &root, Instant::now());
        let opened = p.chooser.as_ref().unwrap().opened_at;
        assert_eq!(
            p.on_chooser_key(press(KeyCode::Enter), &root, opened + CHOOSER_GUARD),
            Outcome::None
        );
        assert_eq!(p.command_args(&root), vec!["update"]);
        // `d` -> `update done` is a leaf, so it runs without a separate Enter.
        assert_eq!(
            p.on_key(key('d'), &root, Instant::now()),
            Outcome::Invoke(vec!["update".into(), "done".into()])
        );
    }

    #[test]
    fn unmatched_letter_is_ignored() {
        let root = tree();
        let mut p = Palette::new();
        // No top-level command starts with `z`.
        assert_eq!(p.on_key(key('z'), &root, Instant::now()), Outcome::None);
        assert!(p.path.is_empty());
        assert!(p.chooser.is_none());
    }

    #[test]
    fn question_mark_requests_help() {
        let root = tree();
        let mut p = Palette::new();
        assert_eq!(p.on_key(key('?'), &root, Instant::now()), Outcome::OpenHelp);
    }
}
