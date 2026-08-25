# Model Usage Policy

## Purpose

This policy routes work to the least expensive model that can complete it
reliably without allowing model-cost pressure to silently change the
architecture. Model output is evidence and implementation assistance, not an
architectural authority. Decisions are accepted because their assumptions,
trade-offs, and validation plan are sound.

The policy applies to interactive Codex work, delegated tasks, reviews, and
future automation in the CineHDR GPU Next development line.

Review this policy after the first working GPU Next milestone and whenever the
available model family or subscription-limit behavior changes. Do not silently
substitute another model for an unavailable Architect tier; record the blocked
decision and continue only work that does not depend on it.

## Core rules

1. Use the lowest tier that is appropriate for the task's **risk**, not merely
   for its apparent size.
2. Architecture is decided with `gpt-5.6-sol` and recorded before a lower tier
   implements it.
3. Routine implementation defaults to `gpt-5.6-terra`, not the smallest model.
4. `gpt-5.6-luna` is restricted to bounded, mechanical work with explicit
   acceptance criteria.
5. No model may approve its own result solely because it is the strongest
   available model. Tests, measurements, source evidence, and review remain
   mandatory.
6. If the architecture model is temporarily unavailable, continue independent
   research, tests, fixtures, documentation, and mechanical preparation. Do
   not let a lower tier make the blocked decision implicitly.

## Routing matrix

| Tier | Default configuration | Use for | Do not use for |
| --- | --- | --- | --- |
| Architect | `gpt-5.6-sol`, `high` reasoning | Renderer boundaries, ownership and lifetime, concurrency and synchronization, color-pipeline invariants, libmpv/libplacebo API strategy, dependency strategy, ADR review, milestone failure analysis | Reformatting, repetitive edits, routine test runs, bulk documentation cleanup |
| Engineer | `gpt-5.6-terra`, `medium` reasoning | Bounded feature implementation, normal debugging, test design, refactoring inside an accepted architecture, integration work, code review | Introducing a new backend contract or changing an HDR invariant without an ADR |
| Operator | `gpt-5.6-luna`, `low` reasoning | Renames, manifest checksum updates, generated-file maintenance, translations, formatting, executing prescribed test matrices, small isolated test fixtures | Ambiguous debugging, FFI, GPU synchronization, color science, dependency upgrades with behavioral impact |
| External test Operator | User-designated target-hardware agent (currently Antigravity / Gemini Flash 7 on Intel/NVIDIA) | Execute one immutable validation hand-off, capture raw artifacts and environment facts | Edit code, improvise retries, change dependencies or criteria, interpret color correctness, or accept a row/gate |

`gpt-5.6-sol` with `max` reasoning is exceptional. Use it only when a hard
problem has resisted a normal `high` pass, when contradictory evidence must be
reconciled, or before a difficult-to-reverse milestone decision. The reason for
using `max` must be stated in the task record.

## What counts as architecture in this project

The following changes require the Architect tier before implementation:

- selecting or replacing the libmpv GPU Next integration approach;
- changing the render-backend interface or fallback policy;
- changing ownership, lifetime, threading, fences, frame queues, or FBO/texture
  synchronization;
- changing HDR detection, transfer-function, primaries, peak-luminance, gamut,
  or tone-mapping invariants;
- changing Dolby Vision or HDR10+ capability policy;
- changing the GTK/GDK/Wayland color-state and compositor hand-off design;
- adding, replacing, or substantially upgrading mpv, FFmpeg, libplacebo, GTK,
  or a protocol/FFI dependency;
- changing persistent settings, public configuration, packaging guarantees, or
  supported platform boundaries;
- accepting a performance/correctness trade-off that changes user-visible
  behavior.

An architectural decision must produce a short ADR containing the context,
options considered, selected option, rejected alternatives, risks, rollback
plan, and measurable validation criteria.

## Escalation rules

Escalate from Luna to Terra when any of these becomes true:

- the task is ambiguous or requires choosing between implementations;
- a change crosses module boundaries;
- a test failure is not explained by the task brief;
- the first implementation attempt fails;
- the diff changes behavior rather than only representation or metadata.

Escalate from Terra to Sol when any of these becomes true:

- an architectural item listed above is encountered;
- two bounded implementation/debugging attempts fail for the same reason;
- tests and documentation imply conflicting invariants;
- the proposed fix adds a new global state, background thread, FFI boundary,
  fallback mode, or dependency;
- correctness cannot be demonstrated without choosing a new product or quality
  trade-off.

Escalation is not failure. It is the mechanism that keeps cheaper attempts from
turning into expensive rework.

Two unsuccessful real-hardware executions in one attempt family exhaust its
default execution budget even if their surface-level error messages differ.
Put the evidence row on `HOLD`; do not send a third command until the conditions
in ADR-0006 are met. The failure signature is diagnostic information, not a way
to reset the budget.

## Task hand-off contract

A task sent to Terra or Luna must be bounded and include:

- the exact objective and files in scope;
- the already accepted architectural constraints;
- explicit non-goals;
- commands or checks that demonstrate completion;
- the conditions that require escalation rather than improvisation.

Give lower-tier models the smallest sufficient context packet. Do not spend
tokens sending the full repository history when a decision summary, relevant
files, and tests are sufficient.

## External hardware test hand-off

External test agents are execution capacity, not delegated engineering. The
primary session owns the hypothesis, command, stop condition, and evidence
interpretation. The external Operator receives a copy/paste-ready brief with:

- exact commit, clean-checkout requirement, runtime/dependency identity, target
  GPU selector, fixture identity, and prerequisites;
- one command, one allowed execution, an expected duration, a hard timeout, and
  a stop-on-failure rule;
- required stdout/stderr, exit code, JSON/log paths, hashes, and environment
  facts;
- explicit prohibitions on edits, argument changes, automatic retries,
  dependency installation, threshold changes, and gate sign-off.

The Operator returns artifacts even on failure and does not repair the test in
place. An unexpected prerequisite failure consumes no hardware attempt only
when the renderer/application never starts and the report records that
preflight boundary. Otherwise it is retained as an attempt. The Architect, not
the executor, classifies the result under ADR-0006.

## Validation execution controls

- Split aggregate gates into independently decidable evidence rows. Preserve
  accepted rows when another row fails or is unavailable.
- Gate 2W is the external Wayland/compositor submission track. It cannot be
  inferred from FBO/GDK evidence and cannot force retries of those rows.
- Use at most an initial hardware run and one evidence-driven corrective run in
  an attempt family. A third requires Architect review, a new ADR row revision,
  the strongest feasible local regression check, a falsifiable brief, and
  explicit user approval.
- A rerun is justified only by one named material change and one expected
  discriminating observation. Changed logging, timeout, report wording, or a
  new error string alone does not start a new family.
- Use unit/static, compile, and software/offscreen evidence before target
  hardware. Use a soak only when duration is the unique variable; never use it
  to debug startup or integration.
- A PASS claim must be backed by an oracle that measures that claim. File shape,
  context creation, a mock, or a microbenchmark must be labelled narrowly.
- Only sanitized machine-readable reports with exact provenance can close a
  hardware row. Prose may summarize but cannot replace them.

## Review and verification

- Luna changes require a diff inspection and the prescribed check. Terra can
  perform the review unless an architecture trigger is present.
- Terra changes require relevant automated tests and a review of boundary
  assumptions. Sol reviews milestone integrations and architecture-sensitive
  diffs, not every routine edit.
- GPU/color correctness claims require pixel tests or real-hardware evidence;
  model confidence and screenshots alone are insufficient.
- Performance claims require comparable measurements using the same media,
  configuration, hardware, and observation window.
- A lower-tier implementation that contradicts an ADR is rejected even if its
  tests pass; either restore the decision or reopen the ADR with Sol.

## Budget controls

- Batch related architecture questions into one decision brief for Sol.
- Ask Sol for the decision, risks, invariants, and validation plan; delegate
  mechanical implementation after the decision is recorded.
- Prefer one focused Sol review of a prepared diff over using Sol to generate
  every intermediate edit.
- Stop repeated retries after the escalation threshold instead of increasing
  prompt length indefinitely.
- Move to an independent evidence row when a hardware row reaches `HOLD` or
  `UNAVAILABLE`; do not spend the remaining session rephrasing the same test.
- Parallelize only independent workstreams with non-overlapping ownership.
- Preserve concise ADRs and test evidence so later sessions do not have to
  rediscover settled reasoning.

## Subscription-limit caveat

The model tiers describe capability, latency, and cost positioning. They do not
guarantee how a particular Codex subscription counts every request or token.
This policy therefore optimizes expected resource use and continuity of work;
actual limit consumption should be observed in the product and the routing
adjusted from evidence, without downgrading architectural safety.

During the first milestone, record the routed tier, retries, escalations,
verification result, and whether a stronger model had to redo the work. Use
that evidence to tune this policy instead of assuming that the smallest model
always produces the lowest total consumption.
