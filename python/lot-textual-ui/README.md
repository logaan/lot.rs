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

(In a normal install the `lot` CLI launches this by execing the
`lot-textual-ui` binary.)

## Keybindings

The app's keys come from one central table (`src/lot_textual_ui/keys.py`). You
can remap any of them through LoT config: add a `[keybindings]` table (to your
user config or a vault's config) mapping an **action name** to the **key** that
should trigger it. The merged result is what `lot config get` reports under
`keybindings`, and the app applies it on startup.

```toml
[keybindings]
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

## Development

```sh
uv run ruff check        # lint
uv run ruff format       # format
uv run ruff format --check
uv run pytest            # tests
```

These are also run by the repo-wide `scripts/check` gate when `uv` is
available.
