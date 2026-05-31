---
name: lot-task
description: Work on a LoT "Thing" asynchronously with a human collaborator. Invoke with a Thing ID (e.g. `/lot-task 0190e3b2-...`). Use when asked to act on a Lists of Things task, note, or item via the `lot` CLI.
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

- `task` — describe a task, or add steps/changes to it.
- `doing` — record progress on a task.
- `done` — record the conclusion or final result.
- `archive` — retire the Thing (no body, just a marker).

Create updates with the `lot` CLI, for example:

```bash
echo "Picked up the parts, assembling now" | lot update doing --thing "$ARGUMENTS"
lot update done --thing "$ARGUMENTS" -- "Shipped and confirmed delivered"
```

## How this session works

This session is controlled **asynchronously**. Both you and the user act on the
Thing by adding Updates via the `lot` command. The user may add updates while
you work; re-read the current state with `lot thing get --thing "$ARGUMENTS"`
before acting so you respond to the latest information.

## Access rules

- Interact with the Thing **only** through skills and the `lot` command.
- Do **not** look for or operate on the Thing's folder path directly.

## Getting started

Run this to see the Thing's current computed state, then decide what update (if
any) to add:

```bash
lot thing get --thing "$ARGUMENTS"
```
