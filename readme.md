# Lists of Things (LoT)

## 1. Config

1. If a `.lot.toml` file exists in the current working directory it is used
   instead of the user config. This lets a project point `lot` at its own
   vault. The project file is never auto-created.
1. Otherwise config is read from `~/.config/lot/config.toml` (respecting
   `XDG_CONFIG_HOME`)
1. If no file exists then `./data/config.example.toml` is copied into that location

## 2. Vault

1. Path is configured using `vault.path`
1. If the vault does not exist then
    1. The folder is created
    1. A new `readme.md` is created from `./data/new-vault-readme.md`
    1. The folder is turned into a git repo with `git init`
    1. The readme is committed.
1. The vault is used to store Things.
1. It is a [git] repository.

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

### 5.1. Thing

1. `lot thing` is the sub command for working with Things.

#### 5.1.1 New

1. `lot thing new` creates a new thing.
1. A name can be passed as arguments and contents can be piped in:

    ```bash
    echo "These are the contents" | lot thing new This is the name
    ```

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
1. After creating the Thing it will be committed to the vault's git repo.

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

   - doing [This is the name](lot:6Ic9Cg6kx0Xk2hQhVz3aBd)
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
     status: doing
     children:
     - name: A child thing
       id: lot:1Ab2Cd3eF4Gh5Ij6Kl7Mn
       status: note
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
      echo "This is\nan update" | lot update doing --thing "lot:6Ic9Cg6kx0Xk2hQhVz3aBd"
      ```

    1. Or as a single line after `--`:

       ```bash
       lot update doing --thing "lot:6Ic9Cg6kx0Xk2hQhVz3aBd" -- "This is an update"
       ```
       
    1. It is an error to pass both.
1. It prints the new update's `update-id` so it can be referenced by scripts.
1. Updates should not be edited.
1. Newly created updates will be committed to the vault's git repo.

The update types form the lifecycle `note` → `work` → `doing` → `info` →
`done`. The `note` type is the automatic first update created by
`lot thing new` (it carries the `task-id`); the rest are created with
`lot update`.

#### 5.2.1. Work

1. `lot update work` creates a new `work` update.
1. Its contents describe a task.
1. Multiple `work` updates represent changes to the task, or additional steps
   that should be taken.
1. `work-at` will be set with the current `ISO 8601` date time.

#### 5.2.2. Doing

1. `lot update doing` creates a new `doing` update.
1. Its contents describe progress on a task.
1. Multiple `doing` updates may be created as a task progresses.
1. `doing-at` will be set with the current `ISO 8601` date time.

#### 5.2.3. Info

1. `lot update info` creates a new `info` update.
1. Its contents describe the conclusion and final result of a task.
1. Multiple `info` updates may be created as a result of a task being resumed
   after initial completion.
1. `info-at` will be set with the current `ISO 8601` date time.

#### 5.2.4. Done

1. `lot update done` creates a new `done` update, retiring the Thing.
1. It should have no contents other than its front matter.
1. `done-at` will be set with the current `ISO 8601` date time.

### 5.3. Claude

1. `lot claude` is the sub command for interacting with [Claude].
1. If called with `--help` or no arguments it will list its sub commands.

[Claude]: https://claude.ai/

#### 5.3.1. Install

1. `lot claude install` will install the LoT skills for the user.

#### 5.3.2. Send

1. `lot claude send` will send a thing to Claude.
   1. It takes the Thing's `task-id` as a positional argument.
   1. A new `claude --bg` session is started that uses the `/lot-task` skill.

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

1. This first version only implements these CLI commands
1. The CLI will be written in Rust
1. In the future there will be TUI and Web interfaces
1. The core logic (non-cli specific code) should be written in a separate module
   from the CLI so that it can be cleanly re-used when those future versions are
   written.

## 8. Deferred tasks

These items may be done in the future.

1. [ ] Build and release using Github workflows
1. [ ] A personal Homebrew tap repository with a `lot` formula
1. [ ] A website for the project that documents the file format and tools.
1. [ ] Compile the core logic to a WebAssembly Component and publish it for
       cross language use.
