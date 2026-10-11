# CineHDR Agent Instructions

This is the ChatGPT/Codex entry point. Follow `docs/MODEL_POLICY.md` for all
model routing, shared safety, and escalation. Gemini uses `GEMINI.md`.

- Current profile: **Gemini-first** for bounded engineering, tests, docs and
  proposals. Honor the user's Gemini-only budget preference; do not automatically
  launch GPT agents. An explicitly assigned Codex task authorizes that task.
- When routed to GPT-6, use `gpt-6-luna` low for mechanical/low-risk prescribed
  work, `gpt-6-sol` medium for bounded difficult engineering and high for proposals
  or substantial reviews. Reserve `gpt-6-astra` high for the hardest questions or
  high-risk review; difficult implementation needs an explicit assignment.
- Engineer, Reviewer and Operator are roles, not model restrictions. A Reviewer
  task is review-only. Sensitive work needs independent acceptance of its ADR
  and changeset by a qualified separate session/agent that did not author it.
  Astra high, Sol high or a capable available Gemini may review. No mandatory
  Sol -> Astra sequence applies; no author may accept its own sensitive work.
- Never silently substitute or upgrade models. After one failed dispatch,
  report it; use a named capable alternative only within the authorized route,
  otherwise continue independent work pending a user routing decision.
- Default to one active implementation agent plus at most one independent
  prescribed-check Operator, with non-overlapping file ownership and a short
  context packet. Batch one review per coherent changeset. Stop after two
  materially distinct local attempts at the same failure for independent
  root-cause/plan review or HOLD, not an automatic premium-model chain.
- Policy/model changes do not reset attempt counts, release HOLD, accept pending
  patches or authorize hardware runs. GNOME is the current debugging target;
  KDE follows after the core path is verified on GNOME.
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
  playback changes, and use one bounded discriminating observation. Do not
  request /dev/dri write access; the user or authorized external Operator performs
  real GPU/Wayland executions when necessary.
- Follow `docs/adr/0006-bounded-validation-governance.md` for every real-GPU,
  compositor, long-running, or externally executed validation task.
- Treat a gate as independent evidence rows. A failed, unavailable, or deferred
  row must not erase accepted rows or force unrelated retries; Gate 2W remains
  separate from the CineHDR-owned Gate 2 boundary.
- Fixed numerical attempt limits are removed. Every new real-hardware run
  requires a concrete hypothesis, a material difference from the previous run,
  an expected discriminating signal, and preservation of raw artifacts.
  Unchanged repeats without diagnostic purpose are strictly prohibited.
  Preserve preflight checks, user instance protection, and evidence standards;
  governance rule changes do not release existing HOLD rows.
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
