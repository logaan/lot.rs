"""The stock update types ``lot`` seeds into a new vault's config.

``lot`` has no fallback set (types are entirely config-defined), so the UI
models no longer carry these; tests that want a realistic seeded-vault
config build one from here instead.
"""

from lot_textual_ui.models import UpdateType


def stock_update_types() -> list[UpdateType]:
    """The seeded lifecycle ``note`` → ``work`` → ``info`` → ``done``."""
    return [
        UpdateType(name="note", takes_body=True, terminal=False),
        UpdateType(name="work", takes_body=True, terminal=False),
        UpdateType(name="info", takes_body=True, terminal=False),
        UpdateType(name="done", takes_body=False, terminal=True),
    ]
