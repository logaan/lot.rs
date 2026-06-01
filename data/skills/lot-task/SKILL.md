---
name: lot-task
description: Work on a LoT "Thing" asynchronously with a human collaborator. Invoke with a Thing ID (e.g. `/lot-task lot:6Ic9Cg6kx0Xk2hQhVz3aBd`). Use when asked to act on a Lists of Things task, note, or item via the `lot` CLI.
---

# LoT Task

You are collaborating on a **Thing** from the user's *Lists of Things (LoT)* vault.
The Thing you are working on has this ID:

```
$ARGUMENTS
```

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

Create updates with the `lot` CLI, for example:

```bash
echo "Picked up the parts, assembling now" | lot update work --thing "$ARGUMENTS"
lot update info --thing "$ARGUMENTS" -- "Shipped and confirmed delivered"
```

## How this session works

This session is controlled **asynchronously**. Both you and the user act on the
Thing by adding Updates via the `lot` command. The user may add updates while
you work; re-read the current state with `lot thing get "$ARGUMENTS"`
before acting so you respond to the latest information.

## Access rules

- Interact with the Thing **only** through skills and the `lot` command.
- Do **not** look for or operate on the Thing's folder path directly.

## Getting started

Run this to see the Thing's current computed state. It will likely have a task
for you to. As you work on it post `work` updates recording your progress. When
you're done post an `info` update. You would not post a `done` (retire) update during a normal
workflow.

```bash
lot thing get "$ARGUMENTS"
```
