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
- Follow `docs/adr/0006-bounded-validation-governance.md` for every real-GPU,
  compositor, long-running, or externally executed validation task.
- Treat a gate as independent evidence rows. A failed, unavailable, or deferred
  row must not erase accepted rows or force unrelated retries; Gate 2W remains
  separate from the CineHDR-owned Gate 2 boundary.
- Allow at most two real-hardware executions in one validation attempt family
  (the initial run and one evidence-driven correction). After the second
  unsuccessful execution, put the row on `HOLD` and require an Architect review
  plus explicit user approval before requesting any third run.
- A rerun brief must name the prior failure signature, the single material
  change, the expected discriminating evidence, the command, timeout, and
  required artifacts. Never repeat an unchanged soak or hardware command.
- An external test agent is an Operator, not an acceptance authority. It may
  execute an immutable hand-off and return raw artifacts; it must not edit the
  implementation, weaken criteria, choose thresholds, or close a gate.
- Prose, screenshots, mocks, and model summaries cannot close an evidence row
  unless the row explicitly defines them as its oracle. Retain a sanitized,
  machine-readable report and its provenance for every accepted hardware row.
- Do not push, publish, open pull requests, or modify remote repositories
  without explicit user authorization.
- Preserve unrelated user changes and keep the stable implementation usable
  while the GPU Next line is experimental.
