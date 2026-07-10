- `lot claude send` warns (without blocking) when its target has both
  Decisions and Steps children — a decide-built coordination root being
  dispatched as a plain worker — and points at the root's "Update plan and
  begin coordination" child as the thing to send instead.
