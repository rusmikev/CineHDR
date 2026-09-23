# CineHDR Agent Instructions

This is the ChatGPT/Codex entry point. Follow `docs/MODEL_POLICY.md` for all
model routing, shared safety, and escalation. Gemini uses `GEMINI.md`.

- Launch `gpt-6-sol` with medium reasoning for normal bounded engineering
  within an accepted architecture; reserve `gpt-6-luna` with low reasoning for
  explicit mechanical work or prescribed checks.
- `gpt-6-sol` with high reasoning prepares proposals for renderer architecture,
  synchronization, color-pipeline invariants, Wayland/GTK hand-off, Dolby Vision
  policy, and dependency strategy. Do not implement an architecture-sensitive
  change until independent `gpt-6-astra` high review accepts its ADR.
- `gpt-6-astra` is review-only: no implementation edits, routine tests, or
  automatic takeover when an engineer is unavailable. Never silently substitute
  or upgrade models; after one failed dispatch, report it and continue only
  independent work.
- Default to one active implementation agent plus at most one independent
  prescribed-check Operator, with non-overlapping file ownership and a short
  context packet. Batch one Astra review per coherent changeset; revisit only
  a material unresolved concern. Stop after two materially distinct local
  engineering attempts at the same failure and hand off or place it on HOLD.
- Record architectural decisions as ADRs with measurable validation and a
  rollback plan.
- Treat tests, measurements, and source evidence as the acceptance authority,
  not model confidence.
- Do not create entities beyond necessity. Extend existing modules, tests,
  diagnostics and documents first; justify any new abstraction, service,
  setting, script, report format or gate by a concrete unmet requirement.
- Verify every runtime hypothesis live in the actual CineHDR playback path
  before calling it confirmed or fixed. Static analysis and mocks are
  prerequisites, not substitutes; missing live evidence stays unverified.
- Use Video Output Diagnostics and its Copy Report as the single user-facing
  place for video-output checks, failure reasons and evidence gaps. Distinguish
  configured intent from actual state, texture publication from presentation,
  and compositor metadata from measured display output. Unavailable data must
  remain Unknown/Unverified, never a fabricated zero, success or certification.
- Live checks still obey ADR-0006: prefer the current session, make no hidden
  playback changes, use one bounded discriminating observation, and preserve
  attempt limits. Do not request /dev/dri write access; the user or authorized
  external Operator performs real GPU/Wayland executions when necessary.
- Follow `docs/adr/0006-bounded-validation-governance.md` for every real-GPU,
  compositor, long-running, or externally executed validation task.
- Treat a gate as independent evidence rows. A failed, unavailable, or deferred
  row must not erase accepted rows or force unrelated retries; Gate 2W remains
  separate from the CineHDR-owned Gate 2 boundary.
- Allow at most two real-hardware executions in one validation attempt family
  (the initial run and one evidence-driven correction). After the second
  unsuccessful execution, put the row on `HOLD` and require independent Astra
  high review of a Sol high proposal
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
  Store personal paths, media titles, and raw machine identifiers locally;
  public summaries, documentation, and commit messages must remain anonymized.
- Do not push, publish, open pull requests, or modify remote repositories
  without explicit user authorization.
- Preserve unrelated user changes and keep the stable implementation usable
  while the GPU Next line is experimental.
