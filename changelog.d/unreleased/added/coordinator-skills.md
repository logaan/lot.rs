- Three bundled **coordinator** skills for driving a root Thing's subtree of
  child Things across worker sessions: `lot-coordinate-decide` (*Decide, Plan,
  Initiate* — decompose into a Decisions + Steps subtree, post the plan, then
  hand back to the human for sign-off), `lot-coordinate-plan` (*Plan, Act* —
  fully autonomous decompose-and-execute), and `lot-coordinate-act` (*Act with
  existing plan* — execute a pre-built child plan without re-decomposing). Each
  teaches per-child model selection via the `claude-model` preamble field,
  launching children with `lot claude send`, monitoring with
  `lot watch --thing`, treating a child's `info` status as step-complete, and
  deferring code integration to the host project's own workflow docs. (Skill
  content only; the `lot claude coordinate` command and skill embedding/install
  wiring land separately.)
