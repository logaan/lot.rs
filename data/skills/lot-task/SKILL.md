---
description: "Work on a LoT \"Thing\" asynchronously with a human collaborator.
  Invoke with a Thing ID (e.g. `/lot-task lot:6Ic9Cg6kx0Xk2hQhVz3aBd`). Use when
  asked to act on a Lists of Things task, note, or item via the `lot` CLI."
name: lot-task
---

# LoT Task

You are collaborating on a **Thing** from the user's *Lists of Things (LoT)*
vault. The Thing you are working on has this ID:

    $ARGUMENTS

## What a Thing is

A Thing is anything the user might put on a list — a task, a note, a movie to
watch, groceries to buy. Its current state is the result of folding together a
sequence of typed **Updates**.

## What an Update is

An Update is an append-only entry that changes a Thing. You never edit past
updates; you add new ones. The update types you can create are:

- `work` — describe a task, add steps/changes to it, or record progress on it.
- `info` — record the conclusion or final result.
- `done` — retire the Thing (no body, just a marker).

Create updates with the `lot` CLI. **Always pass the body on stdin**, and for
anything multi-line write it to a file first and redirect it in:

``` bash
echo "Picked up the parts, assembling now" | lot update work --thing "$ARGUMENTS"

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

## How this session works

This session is controlled **asynchronously**. Both you and the user act on the
Thing by adding Updates via the `lot` command. The user may add updates while
you work; re-read the current state with `lot thing get "$ARGUMENTS"` before
acting so you respond to the latest information.

## Which vault

The request that started this session came from a specific vault. `lot claude
send` records it in this session's environment as `LOT_VAULT_PATH` (and the
Thing's id as `LOT_THING_ID`), and every `lot` command honours `LOT_VAULT_PATH`
over any config file — so `lot` commands here hit the right vault from **any**
working directory, including git worktrees. Do not unset or override these
variables.

If `LOT_VAULT_PATH` is *not* set (e.g. this skill was invoked by hand), `lot`
falls back to its config resolution, which depends on the working directory; in
that case run `lot` from the directory you were started in. If `lot thing get`
reports `no thing found with id ...`, it is resolving the wrong vault — not a
missing Thing.

## Access rules

- Interact with the Thing **only** through skills and the `lot` command.
- Do **not** look for or operate on the Thing's folder path directly.

## Getting started

Run this to see the Thing's current computed state. It will likely have a task
for you to. As you work on it post `work` updates recording your progress. When
you're done post an `info` update. You would not post a `done` (retire) update
during a normal workflow.

``` bash
lot thing get "$ARGUMENTS"
```
