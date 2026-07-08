# lot-textual-ui

A [Textual](https://textual.textualize.io/) TUI front-end for LoT (Lists of
Things). This is a Python sub-project of the LoT monorepo, managed with
[`uv`](https://docs.astral.sh/uv/).

Requires Python 3.12+.

## Setup

Install dependencies (creates a virtualenv and resolves from `uv.lock`):

```sh
uv sync
```

## Running

Start the app via the `lot-textual-ui` console script:

```sh
uv run lot-textual-ui
```

(In a normal install the `lot` CLI launches this — via `lot interface` — by execing a
`lot-textual-ui` binary found next to `lot` or on PATH. The repo's
`scripts/install` symlinks `~/bin/lot-textual-ui` to the launcher at
`bin/lot-textual-ui`, which resolves this project and runs the console script
through `uv run` from any directory.)

## Serving to web browsers

A second console script, `lot-textual-ui-web` (`src/lot_textual_ui/web.py`),
serves the app to web browsers using self-hosted
[textual-serve](https://github.com/Textualize/textual-serve): it starts one
fresh `lot-textual-ui` process per browser session. `lot web` is the
user-facing entry point (it resolves `lot-textual-ui-web` next to `lot` or on
PATH, exactly like `lot interface`; `scripts/install` symlinks the launcher at
`bin/lot-textual-ui-web` into `~/bin`). During development:

```sh
uv run lot-textual-ui-web [--host HOST] [--port PORT]
```

- The default bind is `0.0.0.0:8000`, so other machines on the local network
  can reach the UI. **There is no authentication or encryption** — anyone who
  can reach the port can read and change the vault. Use `--host 127.0.0.1` for
  local-only serving.
- On startup it prints the URL(s) to open (localhost plus the machine's LAN
  address for a wildcard bind; the LAN address is also used as textual-serve's
  `public_url`, so the served page's websocket connects to a routable address
  rather than `0.0.0.0`).
- Environment contract for the served app processes (textual-serve copies the
  server's environment into each session's subprocess):
  - `LOT_VAULT_PATH` — forwarded by `lot web`, so every session (and every
    `lot` subprocess it spawns) hits the same vault.
  - `LOT_TEXTUAL_WEB=1` — set by `lot web` and by the entry point itself, so
    the app can detect it is being served to a browser rather than run in a
    terminal and adapt. `webmode.is_web_mode()` is the single helper the app
    consults for this (it reads the variable at call time, so tests fake
    either mode with a plain `monkeypatch`); see "Web mode" below for what
    changes.

### Web mode

A served session is a real `lot-textual-ui` process, so almost everything
works exactly as in a terminal — the trees, detail pane, forms, batch
operations, vault switching, live `lot watch` updates, mouse
scroll/click/selection, the `ctrl+p` palette, and the `space` command
navigator are all in-terminal features that textual-serve relays unchanged.
Two things differ when `is_web_mode()` is true:

- **The `$EDITOR` escape hatch is disabled.** `ctrl+o` in the new-Thing /
  new-Update / batch forms suspends the app and hands the terminal to
  `$EDITOR` — but a browser session has no local terminal to hand over
  (`App.suspend` is unsupported over the web transport). In web mode the
  binding is hidden from the form footers and pressing the chord anyway shows
  a notice instead; `editor.edit_in_editor()` is additionally a hard no-op as
  a backstop, so no code path can stall a session on a server-side editor.
- **Clipboard copies are a handoff, not a guarantee.** The copy actions
  (`y`/`Y`, the update variants, and `c` for the mouse text-selection) emit
  OSC 52, which textual-serve relays to xterm.js in the browser; its clipboard
  addon passes the text to `navigator.clipboard.writeText`. That API only
  exists on secure pages, so the copy lands when the page is `http://localhost`
  (or served over HTTPS) **and silently does nothing over plain HTTP on a LAN
  address** — the default `lot web` bind. The app cannot observe which of the
  two happened, so the web-mode toast says "sent to the browser clipboard"
  rather than claiming success. (Textual's in-app mouse text *selection* still
  works either way; only the final write to the OS clipboard is gated. There
  is no browser-native page selection — the UI renders into an xterm.js
  canvas.)

## Keybindings

The app's keys come from one central table (`src/lot_textual_ui/keys.py`). You
can remap any of them through LoT config: add a `[tui.keybindings]` table (to
your user config or a vault's config) mapping an **action name** to the **key**
that should trigger it. The merged result is what `lot settings get` reports
under `keybindings`, and the app applies it on startup.

```toml
[tui.keybindings]
cursor_down = "s"        # move down with `s` instead of `j`
cursor_up = "w"
focus_right = "L"        # a single key...
new_thing = "n,insert"   # ...or several, comma-separated
```

Semantics:

- The value replaces the key for that action; the old default key stops
  triggering it. A comma-separated value binds several keys to the action.
- An entry naming an action the app doesn't bind is ignored.
- Textual's built-in `ctrl+q` (quit) and `ctrl+c` are not part of this table
  and are never remapped, so they always work as an escape hatch.

Remappable action names (each is bound to a default key out of the box):

| Action | Default | What it does |
| --- | --- | --- |
| `quit` | `q` | Leave the app. |
| `command_palette` | `ctrl+p` | Open the fuzzy command palette. |
| `command_nav` | `space` | Open the command navigator (see below). |
| `toggle_help_panel` | `ctrl+question_mark` (also `ctrl+shift+question_mark`) | Show/hide Textual's keys & widget help panel (also in the `ctrl+p` palette as "Keys"). |
| `cursor_down` | `j` | Move down one row in the focused pane. |
| `cursor_up` | `k` | Move up one row in the focused pane. |
| `cursor_top` | `g` | Jump to the first row of the focused pane. |
| `cursor_bottom` | `G` | Jump to the last row of the focused pane. |
| `focus_right` | `l` (also `enter`) | Drill in one column to the right. |
| `focus_left` | `h` (also `backspace`) | Drill out one column to the left. |
| `new_thing` | `n` | Create a new top-level Thing. |
| `new_child_thing` | `a` | Create a Thing under the current selection. |
| `copy_thing_uri` | `y` | Copy the selected Thing's `lot:` id. |
| `copy_thing_path` | `Y` | Copy the selected Thing's filesystem path. |
| `copy_selection` | `c` | Copy the current mouse text-selection. |
| `toggle_update` | `z` | Collapse/expand the focused update. |
| `toggle_mark` | `x` | Mark/unmark the highlighted Thing (multi-select). |
| `toggle_mark_siblings` | `X` | Mark/unmark the highlighted Thing and all of its siblings at once. |
| `clear_marks` | `u` | Unmark every marked Thing. |
| `batch_move` | `m` | Move every marked Thing (picks a destination). |
| `batch_archive` | `d` | Archive every marked Thing (asks first). |
| `batch_update` | `U` | Append one new Update to every marked Thing. |

## Adding updates

Update creation is type-specific — there is no general "new update" form with
a type picker. Both the fuzzy palette (`ctrl+p`) and the command navigator
offer one entry per update type (`update work`, `update info`, `update done`,
plus any custom types from `[[update-types]]` config — see the main readme
§1.3), discovered from `lot help`. Picking one acts on the Thing you're
looking at (the centre column's active item):

- A **body-taking** type (`work`/`info`, or a custom type) opens a small form
  fixed to that type: just the markdown body, with the `ctrl+o` `$EDITOR`
  escape hatch. `ctrl+s` submits, `escape` cancels.
- A **bodyless** type (`done`, or a custom `takes-body = false` type) is
  recorded immediately with no form at all — e.g. `ctrl+u` `d` marks the
  in-view Thing done in two keystrokes.

Which types exist — and whether each takes a body — is discovered from
`lot settings get` (see "Update types" below).

## Multi-select and batch operations

Both tree columns support marking multiple Things and acting on the whole
marked set at once. `space` is the command navigator's leader key, so the mark
toggle lives on `x` (the file-manager convention), remappable like every other
action above.

- `x` toggles a mark on the Thing under the focused tree's cursor; marked rows
  show a `●` indicator in both columns (a mark is per-Thing, not per-row).
  `X` toggles the whole sibling group at once — the highlighted Thing and every
  Thing sharing its parent (its fellow roots when it is itself a root); if they
  are all already marked it unmarks them instead. `u` clears all marks.
- Three batch actions run over the marked set (also in the `ctrl+p` palette as
  **Move/Archive/Update marked Things**):
  - **Move** (`m`) — pick a destination Thing (or "Top level (vault root)")
    from a tree-shaped list, then each marked Thing is moved via
    `lot thing move <id> --parent <target>` (or `--root`). The marked Things
    themselves are not offered as destinations.
  - **Archive** (`d`) — a confirmation dialog states how many Things (and all
    their descendants) will be archived, then each is removed via
    `lot thing archive <id>`. The CLI refuses when `vault.auto-commit` is
    `false`; that error is shown per item.
  - **Update** (`U`) — the update **type** is picked first in the command
    navigator (opened inside `update`, exactly the type-select step of the
    single-Thing `ctrl+u` flow), then one Update is applied to every marked
    Thing — e.g. mark a handful of finished tasks and record one `done` across
    all of them. A body-taking type (`work`/`info`, or a custom type) then
    opens a body-only form (no type selector); a bodyless type (`done`-likes)
    skips the form entirely and is recorded straight away.
- Batches run sequentially with progress in the header. A failed item never
  aborts the rest: failures are collected and reported at the end with each
  Thing's name and the CLI's error text, and they keep their marks so the
  batch can be re-run after fixing the cause. Successes are unmarked as they
  land, and marks can never point at a Thing that no longer exists (archived
  or deleted Things are pruned from the mark set automatically).
- **Archive done Things** (palette only, no marks needed) — a confirmation
  dialog, then one `lot vault archive` call archives every Thing in a
  terminal status (`done`, or a custom update type with `terminal = true`),
  committing all the deletions in a single commit. The vault is reloaded and
  the number archived is reported; the CLI's refusal under
  `vault.auto-commit = false` (or any other error) is shown verbatim.

## Update types

The update flows offer the full **effective set** of update types, not just
the built-ins: the creatable built-ins (`work`/`info`/`done`) plus every
custom type defined in config as `[[update-types]]` tables (readme §1.3).
Discovery goes through `lot settings get`'s `update-types` key — the app
never reads config files — and the flags drive the behaviour:

- `takes-body = false` types are bare markers like `done`: picking one
  records the update immediately with no form (see "Adding updates" above) —
  in both the single-Thing and the batch flow, where it is applied to every
  marked Thing with no content sent.
- `terminal = true` types retire the Thing's status; the command navigator
  shows each type's `about` text so its effect is clear when picking it.

Custom types appear as `update <name>` commands in the `ctrl+p` palette and
the command navigator (both discovered from `lot help --format=yaml`) — used
alike for a single Thing and for the marked-set batch; picking a body-taking
one opens the type-fixed form described above. A Thing whose status is a
custom type name shows it spelled out in the trees with a fallback colour.

The set is read from the config loaded at startup and re-read on every vault
switch, so a vault's own custom types are offered as soon as the app points at
it; a mid-session config-file edit needs a re-switch (or restart) to show up,
like every other config key.

## Command navigator

An alternative to the fuzzy `ctrl+p` palette that mirrors the CLI's
command/sub-command hierarchy (`src/lot_textual_ui/command_nav.py`):

- `space` opens a command selector at the top level of the `lot` command tree
  (discovered at runtime from `lot help --format=yaml`).
- `ctrl+<first letter of a top-level command>` opens it already *inside* that
  command — e.g. `ctrl+t` lands in `lot thing`, so `ctrl+t` `n` runs
  `lot thing new`. These shortcuts are derived from the discovered tree, not
  bindings, so they are not remappable; `ctrl+c`/`ctrl+p`/`ctrl+q`/`ctrl+z`
  keep their usual meanings and are never treated as shortcuts.
- With it open, type a command's **first letter** to walk down the tree. A
  letter that lands on a command with no sub-commands runs it straight away
  (through the same path as a palette pick, so commands that need input still
  open their form). `backspace` undoes one step; `escape` clears the input,
  then closes.
- When a letter matches more than one command a chooser appears: move the
  highlight with the arrows (or `j`/`k`) and confirm with `enter` (ignored for
  the first 250 ms, so a stray Enter can't pick an option you haven't seen).

## Vaults

The app starts against the vault LoT resolves from config/environment
(`LOT_VAULT_PATH`), and can switch to any other vault you list in config without
restarting. Declare the vaults under `[tui]` as `[[tui.vaults]]` entries (in your
user config or a vault's config); each has a `path` and an optional `name`:

```toml
[[tui.vaults]]
path = "~/lot-vault"
name = "Personal"

[[tui.vaults]]
path = "~/work/wavelet/.lot-vault"
name = "Wavelet"
```

`lot settings get` reports the merged list (under `vaults`) plus the active
`vault-path`, which the app reads on startup. To switch, open the command palette
(`ctrl+p`) and run **Switch vault**: pick a vault from the list (the active one is
marked) and the whole UI — tree, detail pane, and live `lot watch` — reloads
against it. The active vault is shown in the header. Switching to a vault that
can't be loaded leaves you on the current one with an error notification. If no
vaults are configured, the command tells you to add some.

## Development

```sh
uv run ruff check        # lint
uv run ruff format       # format
uv run ruff format --check
uv run pytest            # tests
```

These are also run by the repo-wide `scripts/check` gate when `uv` is
available.
