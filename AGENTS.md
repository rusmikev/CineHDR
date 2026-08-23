# CineHDR Agent Instructions

Follow `docs/MODEL_POLICY.md` for all model routing and escalation.

- Route renderer architecture, synchronization, color-pipeline invariants,
  Wayland/GTK hand-off, Dolby Vision policy, and dependency strategy to
  `gpt-5.6-sol` with high reasoning before implementation.
- Use `gpt-5.6-terra` with medium reasoning for normal bounded engineering
  within an accepted architecture.
- Use `gpt-5.6-luna` with low reasoning only for explicit mechanical tasks.
- A lower tier must escalate when it encounters an architectural choice; it
  must not invent or silently broaden the design.
- Record architectural decisions as ADRs with measurable validation and a
  rollback plan.
- Treat tests, measurements, and source evidence as the acceptance authority,
  not model confidence.
- Do not push, publish, open pull requests, or modify remote repositories
  without explicit user authorization.
- Preserve unrelated user changes and keep the stable implementation usable
  while the GPU Next line is experimental.
