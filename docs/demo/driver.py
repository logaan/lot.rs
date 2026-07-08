"""Drive the LoT TUI headlessly and capture the demo gif's SVG frames.

Run by docs/demo/generate (via ``uv run --project python/lot-textual-ui``),
which supplies the environment: ``DEMO_VAULT`` (the throwaway vault),
``DEMO_FAKE_HOME`` (a home whose lot config names the vault "Personal"), and
``DEMO_FRAMES`` (where to write the frames).

The real app runs against the real ``lot`` CLI; the single stub is
``claude send``, which records the launch ``work`` update in the vault and
returns a canned launch reference instead of spawning an actual ``claude``
session. After each scripted step a screenshot is exported, and
``manifest.txt`` pairs every frame with how long the gif should hold it.
"""

import asyncio
import os
import pathlib

VAULT = pathlib.Path(os.environ["DEMO_VAULT"])
FRAMES = pathlib.Path(os.environ["DEMO_FRAMES"])
FAKE_HOME = pathlib.Path(os.environ["DEMO_FAKE_HOME"])

os.environ["HOME"] = str(FAKE_HOME)
os.environ["XDG_CONFIG_HOME"] = str(FAKE_HOME / ".config")
os.environ["LOT_VAULT_PATH"] = str(VAULT)

from lot_textual_ui.app import LotTextualApp  # noqa: E402
from lot_textual_ui.lot_cli import LotCli  # noqa: E402

LAUNCH_REFERENCE = """backgrounded · 8f31c2aa
  claude attach 8f31c2aa
  claude logs 8f31c2aa
  claude stop 8f31c2aa"""


class DemoLotCli(LotCli):
    """Real LotCli except `claude send` doesn't spawn a real Claude session."""

    async def claude_send(self, model: str, thing_id: str) -> str:
        body = (
            f"Launched a background Claude session (model: {model}).\n\n"
            "Launch output:\n\n```text\n" + LAUNCH_REFERENCE + "\n```"
        )
        await self.add_update("work", thing_id, body)
        return LAUNCH_REFERENCE


class Recorder:
    def __init__(self, app):
        self.app = app
        self.n = 0
        self.manifest = []

    def snap(self, duration: float) -> None:
        self.n += 1
        name = f"{self.n:03d}.svg"
        (FRAMES / name).write_text(self.app.export_screenshot())
        self.manifest.append((name, duration))

    def write_manifest(self) -> None:
        lines = [f"{name} {duration}" for name, duration in self.manifest]
        (FRAMES / "manifest.txt").write_text("\n".join(lines) + "\n")


async def main() -> None:
    app = LotTextualApp(lot_cli=DemoLotCli())
    async with app.run_test(size=(108, 30), notifications=True) as pilot:
        rec = Recorder(app)
        await pilot.pause(1.5)
        rec.snap(2.0)  # opening view

        # Walk down the root list to "Plan the camping trip".
        for hold in (0.7, 0.5, 0.5, 1.2):
            await pilot.press("j")
            await pilot.pause(0.3)
            rec.snap(hold)

        # Drill into it: focus the centre tree.
        await pilot.press("l")
        await pilot.pause(0.5)
        rec.snap(1.5)

        # Move down to "Research the weather forecast"; let its update
        # thread show in the detail pane.
        for hold in (0.8, 0.8, 2.2):
            await pilot.press("j")
            await pilot.pause(0.4)
            rec.snap(hold)

        # Add a child Thing under it with `a`, typed live.
        await pilot.press("a")
        await pilot.pause(0.4)
        rec.snap(1.2)
        for ch in "Check the fire danger rating":
            await pilot.press(ch if ch != " " else "space")
            rec.snap(0.07)
        rec.snap(0.8)
        await pilot.press("ctrl+s")
        await pilot.pause(1.0)
        rec.snap(2.0)  # the new child in the tree

        # Back up to "Research the weather forecast" and send it to Claude
        # via the command navigator: space → claude → send → fable.
        await pilot.press("k")
        await pilot.pause(0.4)
        rec.snap(1.5)
        await pilot.press("space")
        await pilot.pause(0.4)
        rec.snap(1.6)
        await pilot.press("c")
        await pilot.pause(0.3)
        rec.snap(1.2)
        await pilot.press("s")
        await pilot.pause(0.3)
        rec.snap(1.2)
        await pilot.press("f")
        await pilot.pause(1.2)  # launch lands: reload + toast
        rec.snap(3.0)
        await pilot.pause(6.0)  # toast expires; clean closing shot
        rec.snap(2.0)

        # Focus the detail pane and jump to the bottom: the launch update.
        await pilot.press("l")
        await pilot.pause(0.3)
        await pilot.press("G")
        await pilot.pause(0.5)
        rec.snap(4.5)

        rec.write_manifest()
        print(f"captured {rec.n} frames")


asyncio.run(main())
