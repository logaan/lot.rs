# Lists of Things (LoT)

## 1. Config

### 1.1. Vault-path resolution

1. If the `LOT_VAULT_PATH` environment variable is set (and not blank) its value
   is used as the vault path, taking precedence over every config file. A
   leading `~` is expanded against the user's home directory, and no config file
   is read or created.
1. Otherwise, if a `.lot.toml` file exists in the current working directory it is
   used instead of the user config. This lets a project point `lot` at its own
   vault. The project file is never auto-created.
1. Otherwise config is read from `~/.config/lot/config.toml` (respecting
   `XDG_CONFIG_HOME`)
1. If no file exists then `./data/config.example.toml` is copied into that location

### 1.2. Front-end settings and the `[tui]` table

1. Front-ends (the TUIs) never read config files directly — they read the
   effective config through `lot config get` (see section 5.5). This keeps all
   merge logic in one place (`lot-core`).
1. Config carries three front-end settings, all under a `[tui]` table and all
   optional (a config with no `[tui]` table is valid; a front-end supplies its
   own defaults for anything left unset):
    1. `tui.theme` — a string naming the colour scheme / theme.
    1. `tui.keybindings` — a table of `action = "key"` overrides. Only the
       actions listed are overridden; the rest keep the front-end's defaults.
    1. `tui.vaults` — an array of `[[tui.vaults]]` tables, each with a `path`
       and an optional `name`, listing the vaults a front-end can switch
       between.
1. There are two levels of config, and the `[tui]` table may appear in each:
    1. **User-level** — `~/.config/lot/config.toml` (or the project-local
       `.lot.toml`, per section 1.1). This is the same file that supplies the
       vault path.
    1. **Vault-level** — a `.lot/config.toml` file *inside the resolved vault*
       (i.e. `<vault>/.lot/config.toml`). This is a distinct file from the
       current-directory `.lot.toml` of section 1.1: that one points `lot` at a
       vault, whereas this one lives in the vault and only carries `[tui]`
       overrides. An absent vault-level file means "no overrides".
1. The **effective** `[tui]` settings are the user-level table overlaid by the
   vault-level table, with the **vault winning** field-by-field:
    1. `theme` — the vault-level theme when it sets one, otherwise the
       user-level theme.
    1. `keybindings` — the union of both tables; a binding present in the
       vault-level config overrides the same-named user-level binding, and
       user-only bindings are kept.
    1. `vaults` — **replaced** by the vault-level list when the vault-level
       config sets a non-empty list, otherwise the user-level list is kept
       (replace-if-present).

## 2. Vault

1. Path is configured using `vault.path`
1. `vault.auto-commit` (default `true`) controls whether `lot` runs git at all:
    1. When `true`, changes are committed to the vault's git repo as described
       throughout this spec.
    1. When `false`, `lot` never runs git — the vault is not `git init`ed and
       no commits are made; updates are only written to disk. This suits a
       project-local `.lot.toml` whose vault lives inside the project's own
       repository, letting vault changes be batched into the project's commits
       or PRs.
    1. When `LOT_VAULT_PATH` short-circuits config (see section 1) no config
       file is read, so auto-commit keeps its default of `true`.
1. If the vault does not exist then
    1. The folder is created
    1. A new `readme.md` is created from `./data/new-vault-readme.md`
    1. With auto-commit enabled:
        1. The folder is turned into a git repo with `git init`
        1. The readme is committed.
1. The vault is used to store Things.
1. It is a [git] repository (unless auto-commit is disabled, in which case any
   version control is left to the user — e.g. an enclosing project repo).

[git]: https://git-scm.com/

## 3. Things

1. Are folders containing update files.
1. They may be used to represent anything you'd put in a list.
    1. Eg: tasks, notes, groceries, movies, etc.
1. The current state of a thing can be computed.
    1. Reduce over each update.
    1. Shallow merge frontmatter yaml.
        1. Newer values override older ones.
    1. Append the contents of each markdown file together.

## 4. Update files

1. Are written in [Markdown]
1. They use [YAML Frontmatter] to set properties of the thing.
1. They are sequentially numbered.
1. They are typed.
1. Every update sets an `update-id` in its front matter, uniquely identifying
   that update.

[Markdown]: https://www.markdownguide.org/
[YAML Frontmatter]: https://docs.github.com/en/contributing/writing-for-github-docs/using-yaml-frontmatter

### 4.1. Ids

1. Things (tasks) and updates are identified by a URI of the form `lot:<id>`.
1. `<id>` is a version 7 UUID encoded in [base62], which is always 22
   characters, making a full id 26 characters including the `lot:` scheme.
1. A Thing's id is recorded as `task-id`; an update's own id is recorded as
   `update-id`. Keeping them in separate fields avoids a collision in the
   `note` update, which carries both.

[base62]: https://en.wikipedia.org/wiki/Base62

## 5. CLI

1. The CLI is called `lot`
1. It lets users interact with their Things.
1. It will show its `--help` if called with no arguments.
1. Any command will describe itself and any of its own sub commands if called
   with `--help`.
1. Commands that take a Thing's `task-id` (e.g. `lot thing get`, `lot claude
   send`, and the `--thing` flag on `lot update`) read it from the
   `LOT_THING_ID` environment variable when it is not given on the command line.
   An id passed explicitly always wins.

### 5.1. Thing

1. `lot thing` is the sub command for working with Things.

#### 5.1.1 New

1. `lot thing new` creates a new thing.
1. A name can be passed as arguments and contents can be piped in:

    ```bash
    echo "These are the contents" | lot thing new This is the name
    ```

1. With no name (and an interactive terminal) it opens the user's editor on a
   temporary `.md` file seeded with a template:
    1. The editor is `$VISUAL`, then `$EDITOR`, falling back to `nvim`.
    1. The first line is a markdown h1 (`# `); the text typed after it becomes
       the Thing's name.
    1. The second line is a throwaway one-line comment (stripped on save); the
       Thing's body is written below it.
    1. If the name (the h1) is left empty (or only whitespace) the creation is
       cancelled and no Thing is made.
1. `--editor` composes the contents in the user's editor instead of reading
   stdin:
    1. A temporary `.md` file is opened in `$VISUAL`, then `$EDITOR`, falling
       back to `nvim`.
    1. If the saved file is empty (or only whitespace) the creation is
       cancelled and no Thing is made.
    1. Otherwise the file contents are used as the Thing's contents.
1. `--parent=<lot:id>` creates the Thing as a child of an existing Thing:
    1. The new Thing's folder is created inside its parent's folder.
    1. Things can be nested arbitrarily deep.
1. It prints the new Thing's `id` so it can be referenced by scripts.

1. A new folder is created using the Thing's name.
    1. It is an error if a folder of that name already exists.
1. A `note` update file will be made in the new folder. In that update:
    1. `task-id` will be set with a fresh `lot:<id>` identifying the Thing.
    1. `update-id` will be set with a fresh `lot:<id>` identifying the update.
    1. `note-at` will be set with the current `ISO 8601` date time.
    1. Its contents will be those piped in to `lot thing new`.
1. After creating the Thing it will be committed to the vault's git repo
   (unless `vault.auto-commit` is `false`, see section 2).

#### 5.1.2 Path

1. `lot thing path` will print the path of a thing.
1. It takes the Thing's `task-id` as a positional argument.

#### 5.1.3 Get

1. `lot thing get` will print the computed current state of a thing.
1. It takes the Thing's `task-id` as a positional argument.
1. `--format` selects the output: `yaml` (the default) renders the state as a
   YAML document (frontmatter keys plus a `body` key); `markdown` renders it as
   frontmatter followed by the markdown body.

#### 5.1.4 List

1. `lot thing list` will print a list of all things.
1. `--format` selects the output: `yaml` (the default) or `markdown`.
1. The `markdown` format prints a markdown document:
    1. The vault path is the `h1`.
    1. Things are listed as a nested bullet list, each item being its status
       followed by a markdown link: `- <status> [name](lot:id)`.
    1. Child Things are indented two spaces beneath their parent.

   ```
   # /Users/you/vault

   - work [This is the name](lot:6Ic9Cg6kx0Xk2hQhVz3aBd)
     - note [A child thing](lot:1Ab2Cd3eF4Gh5Ij6Kl7Mn)
   ```

1. The `yaml` format prints a YAML document:
    1. `path` is the vault path.
    1. `things` is a tree of `{ name, id, status, children? }`. The `children`
       key is present only when a Thing has sub-things.
    1. `name` is the `h1` heading of the thing's computed state (the
       human-readable name, with spaces), not the on-disk folder slug. The same
       name is used for the link text in the `markdown` format.

   ```yaml
   path: /Users/you/vault
   things:
   - name: This is the name
     id: lot:6Ic9Cg6kx0Xk2hQhVz3aBd
     status: work
     children:
     - name: A child thing
       id: lot:1Ab2Cd3eF4Gh5Ij6Kl7Mn
       status: note
   ```

#### 5.1.5 Updates

1. `lot thing updates` prints a Thing's whole update thread (not the merged
   state) as a YAML list, oldest first — one entry per update.
1. It takes the Thing's `task-id` as a positional argument, defaulting to
   `LOT_THING_ID` when omitted, like the other `thing` sub-commands.
1. This is the surface a detail view renders as independent, expandable items:
   each entry carries everything needed to display the update without re-reading
   files off disk. Unlike `lot thing get` (which merges the updates into the
   computed state), this keeps every update separate.
1. Each entry is a mapping of:
    1. `update-id` — the update's `lot:<id>`.
    1. `type` — the update's type (`note`, `work`, `info`, or `done`).
    1. `at` — the update's timestamp (re-keyed from the type-specific
       `note-at`/`work-at`/… field).
    1. Any other frontmatter the update recorded (e.g. a `note`'s `task-id`),
       in its original order.
    1. `body` — the raw markdown body.

   ```yaml
   - update-id: lot:033QI8ChY3vGg0spUGXJlp
     type: note
     at: 2026-05-31T14:06:42.600298+00:00
     task-id: lot:6Ic9Cg6kx0Xk2hQhVz3aBd
     body: |
       # This is the name

       These are the contents
   - update-id: lot:0Kj2mn4pq6Rs8tu0vwx2yz
     type: work
     at: 2026-06-01T09:12:03.000000+00:00
     body: |
       On it.
   ```

### 5.2. Update

1. `lot update` is the sub command for working with Updates.
1. `--thing=${task-id}` is used to locate the thing in which to create the update.
1. An update is a single markdown file.
    1. The filename is in the format `001.md`.
    1. Each new update numbers itself one higher than the most recent.
    1. Updates will always set a `status` field in the front matter matching
       their type.
1. Update contents can be passed:
    1. Via standard in:

      ```bash
      echo "This is\nan update" | lot update work --thing "lot:6Ic9Cg6kx0Xk2hQhVz3aBd"
      ```

    1. Or as a single line after `--`:

       ```bash
       lot update work --thing "lot:6Ic9Cg6kx0Xk2hQhVz3aBd" -- "This is an update"
       ```
       
    1. It is an error to pass both.
    1. With neither (and an interactive terminal) it opens the user's editor on
       a temporary `.md` file:
        1. The editor is `$VISUAL`, then `$EDITOR`, falling back to `nvim`.
        1. The file is seeded with a preview of the update — its type and
           timestamp — as throwaway one-line comments (stripped on save), with a
           blank body below them.
        1. The body typed below the comments becomes the update's contents.
        1. If the file is left unchanged (no body is added) the update is
           cancelled and nothing is created.
        1. This applies to `work` and `info`; `done` takes no contents and so
           never opens an editor.
1. It prints the new update's `update-id` so it can be referenced by scripts.
1. Updates should not be edited.
1. Newly created updates will be committed to the vault's git repo
   (unless `vault.auto-commit` is `false`, see section 2).

The update types form the lifecycle `note` → `work` → `info` → `done`. The
`note` type is the automatic first update created by `lot thing new` (it
carries the `task-id`); the rest are created with `lot update`.

#### 5.2.1. Work

1. `lot update work` creates a new `work` update.
1. Its contents describe a task, the next steps to take, or progress made on it.
1. Multiple `work` updates represent changes to the task, additional steps that
   should be taken, or progress as the task is carried out.
1. `work-at` will be set with the current `ISO 8601` date time.

#### 5.2.2. Info

1. `lot update info` creates a new `info` update.
1. Its contents describe the conclusion and final result of a task.
1. Multiple `info` updates may be created as a result of a task being resumed
   after initial completion.
1. `info-at` will be set with the current `ISO 8601` date time.

#### 5.2.3. Done

1. `lot update done` creates a new `done` update, retiring the Thing.
1. It should have no contents other than its front matter.
1. `done-at` will be set with the current `ISO 8601` date time.

#### 5.2.4. Path

1. `lot update path` prints the filesystem path of a single update file.
1. It takes the Update's `update-id` as a positional argument (e.g.
   `lot update path lot:033QI8ChY3vGg0spUGXJlp`).
1. It mirrors `lot thing path`, but resolves an individual update file rather
   than a Thing's folder: the id is searched across every Thing in the vault
   (and their descendants). It is an error if no update carries that id.

### 5.3. Claude

1. `lot claude` is the sub command for interacting with [Claude].
1. If called with `--help` or no arguments it will list its sub commands.

[Claude]: https://claude.ai/

#### 5.3.1. Install

1. `lot claude install` will install the LoT skills for the user.

#### 5.3.2. Send

1. `lot claude send` will send a thing to Claude.
   1. It requires a model sub-command: `sonnet`, `opus`, or `fable`. Run with
      no arguments (bare `lot claude send`) to list them.
   1. Each model sub-command takes the Thing's `task-id` as a positional
      argument and launches the session with that model, passed to `claude` as
      `--model <name>`.
   1. A new `claude --bg` session is started that uses the `/lot-task` skill.
   1. The spawned session's environment carries the request's context —
      `LOT_VAULT_PATH` is set to the resolved vault path and `LOT_THING_ID` to
      the Thing's `task-id` — so `lot` commands run by the receiving Claude hit
      the vault the request came from regardless of their working directory.
      This is the same environment contract the TUI applies to every command it
      invokes.
   1. The launch output of `claude --bg` (its session/job reference) is captured
      and recorded on the Thing as a `work` update — as well as echoed back to
      the caller — so the background session can be traced from the Thing's own
      history. In the update the captured output is wrapped in a ```` ```text ````
      fenced code block so it renders verbatim wherever the history is shown.

### 5.4. Vault

1. `lot vault` is the sub command for working with vaults.
1. If called with `--help` or no arguments it will list its sub commands.

#### 5.4.1. New

1. `lot vault new <path>` initialises a brand-new vault at `<path>`.
   1. It creates the folder, seeds its `readme.md` from
      `./data/new-vault-readme.md`, runs `git init`, and makes the initial
      commit (see section 2).
   1. It then prints the vault path.
1. `<path>` may contain a leading `~`, expanded against the user's home
   directory (the same expansion applied to `vault.path` in the config).
1. It errors if `<path>` already exists: a `new` vault must be fresh.
1. It does not modify any config file and does not write a `.lot.toml`;
   pointing `lot` at the vault is a separate step.

### 5.5. Config

1. `lot config` is the sub command for reading configuration.
1. If called with `--help` or no arguments it will list its sub commands.

#### 5.5.1. Get

1. `lot config get` prints the **effective** front-end configuration — the
   user-level `[tui]` table overlaid by the vault-level `[tui]` table, merged as
   described in section 1.2 (vault wins field-by-field).
1. This is the only way front-ends read config: they never open config files
   directly, so the merge lives in one place.
1. `--format` selects the output: `yaml` (the default) or `markdown`. The
   `yaml` form is the stable, documented shape consumers parse.
1. The `yaml` document always carries these keys (present even when
   empty/unset, so consumers can rely on them):
    1. `theme` — the effective theme string, or `null` when none is configured.
    1. `keybindings` — the merged `action: key` map (`{}` when there are none).
    1. `vaults` — the effective list of vault entries, each a mapping of `path`
       and an optional `name` (`[]` when there are none). The `name` key is
       omitted from an entry that has no name.
    1. `vault-path` — the resolved filesystem path of the currently active
       vault (the one `lot` is operating on).

   ```yaml
   theme: dark
   keybindings:
     quit: q
     down: j
   vaults:
   - name: Personal
     path: ~/lot-vault
   - path: ~/work-vault
   vault-path: /Users/you/lot-vault
   ```

### 5.6. UI

1. `lot interface` launches the terminal user interface.
   1. The TUI is a separate binary, `lot-tui`, built from its own crate
      (`crates/lot-tui`) and kept distinct from the CLI; both are thin
      front-ends over `lot-core`.
   1. `lot interface` runs the `lot-tui` binary, preferring one sitting next to
      the `lot` executable and otherwise falling back to `lot-tui` on `PATH`.
1. `lot pui` launches the Python [Textual](https://textual.textualize.io/) user
   interface.
   1. The Textual TUI is a separate application (`python/lot-textual-ui/`)
      exposing a `lot-textual-ui` console script; like `lot-tui` it is a thin
      front-end that drives the `lot` CLI.
   1. `lot pui` runs the `lot-textual-ui` binary, preferring one sitting next to
      the `lot` executable and otherwise falling back to `lot-textual-ui` on
      `PATH` (mirroring `lot interface`).
   1. It forwards the resolved vault via the `LOT_VAULT_PATH` environment
      variable so every `lot` subprocess the TUI spawns hits the same vault
      regardless of its working directory.
   1. During development `uv run lot-textual-ui` inside `python/lot-textual-ui/`
      runs the app directly; `lot pui` is the user-facing entry point.
   1. `lot interface` stays pointed at the Rust `lot-tui`.
1. It is responsive, choosing a layout from the terminal size:
   1. `wide` — three columns: the Things tree, the selected Thing's sub-things,
      and a detail pane.
   1. `normal` — two columns: the tree and the detail pane.
   1. `tall` — two rows: the tree above the detail pane.
   1. `small` — a single column showing the tree; the detail pane opens as an
      overlay (press <kbd>Enter</kbd>, <kbd>Esc</kbd> to close).
1. The detail pane shows the selected Thing's metadata and its rendered
   markdown body. Links are shown with their URL so terminals can make them
   clickable.
1. Navigation:
   1. Keyboard: <kbd>j</kbd>/<kbd>k</kbd> (or arrows) move the cursor,
      <kbd>J</kbd>/<kbd>K</kbd> scroll the detail pane, <kbd>g</kbd>/<kbd>G</kbd>
      jump to the first/last Thing, and <kbd>q</kbd> quits.
   1. Mouse: click a Thing to select it, and use the scroll wheel over the tree
      or detail pane.
   1. `lot:` ids anywhere in the detail pane are links: they are underlined,
      and clicking one selects that Thing. Clicking an id that is not a Thing
      in the vault (e.g. an update id) reports so in the footer.
   1. Mouse text selection: dragging with the left button over the detail pane
      highlights text, and releasing the button copies it to the system
      clipboard (a brief confirmation shows in the footer). The selection
      copies exactly what is on screen, and is cleared by any keypress, click,
      or scroll — the same way a terminal's native selection behaves.
   1. <kbd>Ctrl-Z</kbd> suspends the TUI to the background like any CLI app; it
      restores the terminal and stops the process, resuming where it left off
      when brought back to the foreground (`fg`).

#### 5.6.1. Command palette

1. The TUI can run any `lot` command via a command palette, opened with the
   <kbd>Space</kbd> leader key (the navigation keys above stay active while it
   is closed).
1. With the palette open you type the **first letter** of a command to walk down
   the command tree. A letter that uniquely lands on a command with no
   sub-commands runs it straight away (e.g. <kbd>Space</kbd> <kbd>t</kbd>
   <kbd>n</kbd> runs `lot thing new`); a letter that lands on a group navigates
   into it instead.
   1. <kbd>Enter</kbd> invokes the current command without navigating further
      (e.g. <kbd>Space</kbd> <kbd>v</kbd> <kbd>Enter</kbd> runs `lot vault`,
      showing its help).
   1. <kbd>Backspace</kbd> undoes the most recent step.
   1. <kbd>Esc</kbd> clears all navigation input, and closes the palette when
      there is nothing left to clear.
1. <kbd>Ctrl</kbd>+a top-level command's first letter is a shortcut into that
   command: it opens the palette as if the letter had been typed after
   <kbd>Space</kbd> (e.g. <kbd>Ctrl-T</kbd> lands in `lot thing`, so
   <kbd>Ctrl-T</kbd> <kbd>n</kbd> runs `lot thing new`).
   1. The letter follows the same rules as typing it in the palette: a
      first-letter collision opens the chooser (below), a leaf runs straight
      away, and a letter matching no top-level command does nothing.
   1. <kbd>Ctrl-C</kbd> (quit) and <kbd>Ctrl-Z</kbd> (suspend) keep their usual
      meanings and are never treated as shortcuts.
1. When a letter matches more than one command (e.g. `u` matches both `update`
   and `ui`) a chooser list appears: move the highlight with the arrows (or
   <kbd>j</kbd>/<kbd>k</kbd>) and confirm with <kbd>Enter</kbd>. To avoid an
   accidental pick, <kbd>Enter</kbd> is ignored for the first 250 ms after the
   list appears. Confirming a command with no sub-commands runs it; confirming a
   group navigates into it.
1. <kbd>?</kbd> opens an overlay showing the whole tree of command shortcuts.
1. The command tree is discovered once at startup from `lot help --format=yaml`,
   so the palette reflects whatever `lot` is installed rather than a hard-coded
   list.
1. Invoking a command stands the TUI aside (like an editor), runs `lot <command>`
   so its output — or an editor it spawns, such as for `lot thing new` — shows
   in the real terminal, waits for a keypress, then resumes and reloads. So, for
   example, a new Thing is created with <kbd>Space</kbd> <kbd>t</kbd>
   <kbd>n</kbd>.
1. When a command's entire output is a single `lot:` id — the machine-readable
   result of `lot thing new` or an `lot update …` — there is nothing for a human
   to read, so the TUI skips both the id and the keypress: it just moves the
   selection to that Thing (an editor it spawned still rendered normally,
   because the CLI points the editor's display at the terminal directly rather
   than at the captured output). Creating a Thing this way therefore lands you
   on it. An id that names no row (an update id) simply leaves the selection
   where it was.
1. Before running a command the TUI sets two environment variables so commands
   have the session's context without further input:
   1. `LOT_THING_ID` — the currently selected Thing's `task-id`.
   1. `LOT_VAULT_PATH` — the path of the vault the TUI is working in.
1. Further user input (typing extra arguments) is not yet possible; a command
   that needs input the environment variables do not supply simply runs and
   shows whatever it prints (for example an error, or an empty update).

#### 5.6.2. Live updates

1. The TUI watches the vault with a filesystem watcher (the OS's native backend,
   not polling) and reloads when anything changes, so edits from any source —
   commands run from the palette, unrelated `lot` invocations, or direct file
   edits — appear without a manual refresh.
1. After every reload the UI state is re-validated so a changed vault cannot
   leave it in an invalid state: the selection is tracked by Thing id and
   re-resolved (cleared or clamped if that Thing has gone), and scrolling is
   reset. The on-disk state always wins.

### 5.7. Watch

1. `lot watch` watches the resolved vault directory and streams one event per
   change on stdout. It is the live-update mechanism for front-ends (notably the
   Python Textual UI) that must never read the vault's on-disk representation
   directly — they consume this stream instead of re-running `lot` commands.
1. It blocks, emitting events as they happen, until interrupted (e.g.
   <kbd>Ctrl-C</kbd> or the consumer closing the pipe).
1. The watcher uses the OS's native filesystem backend (not polling), the same
   mechanism the Rust TUI uses. Rapid bursts of filesystem activity — for
   example a git auto-commit rewriting several files — are debounced and
   coalesced into a single settled batch before events are emitted. Churn inside
   the vault's `.git/` directory is ignored, so the vault's own auto-commits do
   not produce spurious events.
1. No event is emitted for the vault's initial state. A consumer should load the
   baseline itself (e.g. with `lot thing list`) and then apply the stream on top.

#### 5.7.1. Stream format

1. Events are emitted as a stream of YAML documents. Each document is preceded by
   a `---` document-marker line and stdout is flushed after every event, so a
   consumer can read and parse one document at a time off a live pipe.
1. Every event is a YAML mapping whose top-level keys sit at column 0, while all
   nested content (frontmatter values, bodies, the tree) is indented. A body may
   itself contain a `---` line, but always indented inside a block scalar, so a
   bare `---` at column 0 unambiguously marks an event boundary — a consumer can
   split the stream on such lines.

#### 5.7.2. Event schema

1. Each event carries only the **minimum** a consumer needs to patch its own
   in-memory copy of the Things tree incrementally — never a fresh snapshot of
   the whole vault. The shape depends on `kind`:
   1. `kind` — the change: `created`, `modified`, `deleted`, or `reload`.
   1. For `created` and `modified`, the keys, in order, are:
      1. `id` — the affected Thing's `task-id`.
      1. `name` — the affected Thing's display name (the same value the tree in
         `lot thing list` uses).
      1. `status` — the affected Thing's current status.
      1. `parent` — the affected Thing's parent's `task-id`, or absent for a
         top-level Thing. Together `id` + `name` + `status` + `parent` are
         exactly enough to patch one node into a consumer's tree index.
      1. `state` — the affected Thing's recomputed computed-state, identical to
         `lot thing get` (frontmatter keys plus a `body` key).
      1. `updates` — the affected Thing's whole update thread, identical to
         `lot thing updates` (a list, oldest first). `state` and `updates` mean a
         detail view showing the changed Thing needs no follow-up `lot` call.
   1. For `deleted`, the only key is `id`: the consumer removes that id and any
      descendants of it from its index.
   1. `reload` carries no other keys. It is the rare fallback for a settled batch
      that maps to no single Thing (e.g. a vault-level file edit that isn't a
      Thing): the consumer reloads its baseline from scratch (e.g. by re-running
      `lot thing list`) rather than the event embedding the whole tree.
1. A single settled batch can affect more than one Thing (e.g. a creation plus an
   unrelated update); each affected Thing yields its own event.

   ```yaml
   ---
   kind: created
   id: lot:6Ic9Cg6kx0Xk2hQhVz3aBd
   name: This is the name
   status: note
   state:
     status: note
     task-id: lot:6Ic9Cg6kx0Xk2hQhVz3aBd
     body: |
       # This is the name
   updates:
   - update-id: lot:033QI8ChY3vGg0spUGXJlp
     type: note
     at: 2026-05-31T14:06:42.600298+00:00
     task-id: lot:6Ic9Cg6kx0Xk2hQhVz3aBd
     body: |
       # This is the name
   ---
   kind: deleted
   id: lot:6Ic9Cg6kx0Xk2hQhVz3aBd
   ---
   kind: reload
   ```

### 5.8. Help

1. `lot help` prints the usual top-level help.
1. `lot help --format=yaml` prints the full command tree as a YAML document:
   1. Every command and sub-command is included, each nested under its parent.
   1. Each carries the information available from its `--help`: its description
      and its arguments (name, help text, whether required, possible values, and
      any default).
1. The TUI uses this to discover the available commands (see 5.6.1).

## 6. Skills

A set of re-useable skills are available for AI agents.

### 6.1. LoT Task

1. The skill is called `lot-task`
1. It takes a Thing ID.
1. It briefly explains to the agent:
    1. What a Thing is.
    1. What an Update is.
    1. That this session will be primary controlled asynchronously by the user
       and the agent adding Updates to the Thing via the `lot` command.
1. It instructs the agent to read the current state of the Thing by running
   `lot thing get`, and to re-read it before acting so it sees any updates the
   user added while it worked.
1. It does not give the thing path, instead explaining that access and changes
   should be done via skills and the `lot` command.

## 7. Architecture and long term vision

1. The CLI is written in Rust.
1. There is also a TUI (`lot-tui`, launched via `lot interface`) that can browse the
   vault and run any `lot` command from a command palette; a Web interface is
   still planned for the future.
1. The core logic (non-interface-specific code) lives in a separate crate
   (`lot-core`) from the front-ends so that it can be cleanly re-used across the
   CLI, the TUI, and those future versions.

## 8. Deferred tasks

These items may be done in the future.

1. [ ] Build and release using Github workflows
1. [ ] A personal Homebrew tap repository with a `lot` formula
1. [ ] A website for the project that documents the file format and tools.
1. [ ] Compile the core logic to a WebAssembly Component and publish it for
       cross language use.
