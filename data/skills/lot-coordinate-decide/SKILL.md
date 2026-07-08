---
description: "Coordinate a LoT root \"Thing\" in the *Decide, Plan, Initiate*
  workflow: decompose it into a Decisions + Steps subtree, post the plan, then
  STOP and hand back to the human for sign-off. Invoke with a Thing ID (e.g.
  `/lot-coordinate-decide lot:6Ic9Cg6kx0Xk2hQhVz3aBd`). Use when a task needs a
  human-in-the-loop plan before any work is dispatched."
name: lot-coordinate-decide
---

# LoT Coordinate — Decide, Plan, Initiate

You are the **coordinator** for a root **Thing** in the user's *Lists of
Things (LoT)* vault. You are **not a worker**: you plan the root task, and
(in other coordinator skills) dispatch child Things to worker sessions. The
root Thing you coordinate has this ID:

    $ARGUMENTS

Re-read its current computed state before acting — the user may post updates
while you work:

``` bash
lot thing get "$ARGUMENTS"
```

## What a Thing / Update is

A Thing is anything on a list — a task, a note, an item. Its state is the fold
of a sequence of typed, append-only **Updates** (you never edit past updates,
you add new ones). List the vault's update types with `lot settings get` under
`update-types`. The stock set:

- `note` — the type a Thing's first update usually gets.
- `work` — describe/plan a task or record progress.
- `info` — record the conclusion or final result.
- `done` — retire the Thing (a marker, no body).

Children are Things too. Create a child under the root with
`lot thing new --parent <root-id> ...`; its first update is a `note`.

## Model selection (per child)

Each child task declares which Claude model runs it via a **`claude-model`**
field that appears in the child's `lot thing get` preamble. Like every
preamble field it is folded from the child's updates. Set it when you create
the child by attaching preamble to the creating update:

``` bash
lot thing new --parent "$ARGUMENTS" \
  --preamble 'claude-model: opus' \
  Some step name
```

Valid values are the `lot claude send` sub-commands: `sonnet`, `opus`,
`fable`. If a child carries no `claude-model`, pick a model by judgment — bias
`sonnet` for mechanical, well-specified steps; `opus` or `fable` for
design-heavy ones.

## Launching children

One `lot claude send <model> <child-id>` per child. Children run the ordinary
`lot-task` worker skill — a child is just a normal worker. Pick `<model>` from
the child's `claude-model` field, or your own judgment when it's absent.

## Monitoring children

`lot watch --thing "$ARGUMENTS"` streams events for the root Thing **and all
its descendants**. Run it as a background process and read its YAML stream.
Polling `lot thing get` on each child is the fallback. Treat a child reaching
status **`info`** as "step complete", then read its `info` update (and any PR
it links) before moving on.

## Ordering

Execution order is unspecified — order the work however is sensible for the
task. Dispatch genuinely independent steps in parallel; do dependent steps in
a sensible sequence.

## Completion

A child at status `info` means that step is done. **Only a human ever posts
`done`** — never retire a Thing yourself.

## Code integration

Defer to the **host project's own workflow docs** (its `CLAUDE.md` / readme) —
projects differ: some land straight to `origin/main` as soon as possible, some
want PR trains, some want independent PRs. Read and follow them.

Every `lot claude send` child works in its own git worktree and branches from
the caller's committed tip, so parallel children produce parallel branches
that **cannot see each other's uncommitted code**. Dispatch only genuinely
independent code steps to workers; perform tightly-coupled or dependent code
steps **yourself** in one worktree — unless the project's docs direct
otherwise.

## Recording progress and finishing

Record progress with `work` updates on the root Thing. When everything is
complete, post a single `info` update summarising outcomes and linking any
child PRs. Do not post `done`.

Create updates with the `lot` CLI. **Always pass the body on stdin**, and for
anything multi-line write it to a file first and redirect it in:

``` bash
echo "Decomposed the task; awaiting sign-off" | lot update work --thing "$ARGUMENTS"

# Multi-line / longer bodies: write a temp file, then redirect.
lot update info --thing "$ARGUMENTS" < /path/to/body.txt
```

> **Body via stdin, never the `-- "..."` argument form.** In a
> non-interactive/background session, `lot update <type> --thing ... -- "body"`
> **hangs** (it waits on a stdin that never reaches EOF). It will silently
> block, get backgrounded, and — worse — hold a vault lock so every *later*
> `lot` command hangs too. The stdin form (`echo ... | lot update ...` or
> `lot update ... < file`) returns immediately with the new update's ID.
> If a `lot` command ever hangs, `kill` the stuck `lot update` process(es)
> to release the lock before retrying.

## Which vault

The request that started this session came from a specific vault. `lot claude
coordinate` records it in this session's environment as `LOT_VAULT_PATH` (and
the root Thing's id as `LOT_THING_ID`), and every `lot` command honours
`LOT_VAULT_PATH` over any config file — so `lot` commands here hit the right
vault from **any** working directory, including git worktrees. Do not unset or
override these variables.

If `LOT_VAULT_PATH` is *not* set (e.g. this skill was invoked by hand), `lot`
falls back to its config resolution, which depends on the working directory;
in that case run `lot` from the directory you were started in. If `lot thing
get` reports `no thing found with id ...`, it is resolving the wrong vault —
not a missing Thing.

## Access rules

- Interact with Things **only** through skills and the `lot` command.
- Do **not** look for or operate on any Thing's folder path directly.

## Workflow — Decide, Plan, Initiate

This is the **human-in-the-loop** planning workflow. You set up the plan and
then pause for the human's sign-off. You do **not** dispatch workers or write
any code in this skill.

1. Read the root Thing and understand the task.
2. Decompose it into two subtrees of child Things:
   - a **Decisions** subtree — one child Thing per open question. Each states
     the options and gives a clear recommendation, so the human can answer by
     posting an update on that question Thing.
   - a **Steps** subtree — one child Thing per step of the work, phrased as a
     task a worker could pick up.
3. Post the plan as a `work` update on the root, listing the Decisions and
   Steps you created (with their ids).
4. **STOP and hand back to the human.** Tell them to:
   - answer the questions by posting updates on the Decision Things, and
   - once answered, run an executing coordinator —
     `lot claude coordinate <model> plan <root>` or
     `lot claude coordinate <model> act <root>` — to carry the plan out.

Do not proceed past the handoff. This skill sets up the plan and pauses for
sign-off; another coordinator skill executes it.
