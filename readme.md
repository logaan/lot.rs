# Lists of Things (LoT)

## 1. Config

1. Is read from `~/config/lot/config.toml`
1. If no file exists then `./data/config.example.toml` is copied into that location

## 2. Vault

1. Path is configured using `vault.path`
1. If the vault does not exist then
    1. The folder is created
    1. A new `readme.md` is created from `./data/readme.example.md`
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

[Markdown]: https://www.markdownguide.org/
[YAML Frontmatter]: https://docs.github.com/en/contributing/writing-for-github-docs/using-yaml-frontmatter

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
1. A name can be passed after  after `--` and contents can be piped in:

    ```bash
    `echo "These are the contents" | lot thing new -- This is the name`
    ```

1. A new folder is created using the Thing's name.
    1. It is an error if a folder of that name already exists.
1. A `created` update file will be made in the new folder. In that update:
    1. `id` will be set with a `UUID7`
    1. `created-at` will be set with the current `ISO 8601` date time.
    1. Its contents will be those piped in to `lot thing new`.
1. After creating the Thing it will be committed to the vault's git repo.

#### 5.1.1 Path

1. `lot thing path` will print the path of a thing.
1. It takes `--thing=${uuid}` and uses the `id` of the Thing's `created` update.

#### 5.1.1 Get

1. `lot thing get` will print the computed current state of a thing.
1. It takes `--thing=${uuid}`

#### 5.1.1 List

1. `lot thing list` will print a markdown list of things

   ```
   - [This is the name](lot:)
   ```

### 5.2. Update

1. `lot update` is the sub command for working with Updates.
1. `--thing=${uuid}` is used to locate the thing in which to create the update.
1. An update is a single markdown file.
    1. The filename is in the format `001.md`.
    1. Each new update numbers itself one higher than the most recent.
    1. Updates will always set a `status` field in the front matter matching
       their type.
1. Update contents can be passed:
    1. Via standard in:

      ```bash
      echo "This is\nan update" | lot update draft --thing "67F01AD6-DFDD-46A2-8F1C-D114ABF3C584"
       ```

    1. Or as a single line after `--`:
       
       ```bash
       lot update draft --thing "67F01AD6-DFDD-46A2-8F1C-D114ABF3C584" -- "This is an update"`
       ```
       
    1. It is an error to pass both.
1. Updates should not be edited.
1. Newly created updates will be committed to the vault's git repo.

#### 5.2.1. Task

1. `lot update task` creates a new `task` update.
1. Its contents describe a task.
1. Multiple `task` updates represent changes to the task, or additional steps
   that should be taken..
1. `task-at` will be set with the current `ISO 8601` date time.

#### 5.2.2. Doing

1. `lot update doing` creates a new `doing` update.
1. Its contents describe progress on a task.
1. Multiple `doing` updates may be created as a task progresses .
1. `doing-at` will be set with the current `ISO 8601` date time.

#### 5.2.3. Done

1. `lot update done` creates a new `done` update.
1. Its contents describe the conclusion and final result of a task.
1. Multiple `done` updates may be created as a result of a task being resumed
   after initial completion.
1. `done-at` will be set with the current `ISO 8601` date time.

#### 5.2.4. Archive

1. `lot update archive` creates a new `archive` update.
1. It should have no contents other than its front matter.
1. `archived-at` will be set with the current `ISO 8601` date time.

### 5.3. Claude

1. `lot claude` is the sub command for interacting with [Claude].
1. If called with `--help` or no arguments it will list its sub commands.

[Claude]: https://claude.ai/

#### 5.3.1. Install

1. `lot claude install` will install the LoT skills for the user.

#### 5.3.2. Send

1. `lot claude send` will send a thing to Claude.
   1. It takes `--thing=${uuid}`
   1. A new `claude --bg` session is started that uses the `/lot-thing` skill.

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
1. It passes in the current state of the Thing as computed by `lot thing get`.
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
