# Model Usage Policy

## Purpose

This policy routes work to the least expensive model that can complete it
reliably without allowing model-cost pressure to silently change the
architecture. Model output is evidence and implementation assistance, not an
architectural authority. Decisions are accepted because their assumptions,
trade-offs, and validation plan are sound.

This is the shared routing and safety source for ChatGPT/Codex, Gemini
hand-offs, reviews, and future automation in the CineHDR GPU Next line.
`AGENTS.md` is the ChatGPT/Codex entry point; `GEMINI.md` is the Gemini entry
point. Both defer architecture and acceptance to this policy and ADRs.

Review this policy after the first working GPU Next milestone and whenever the
available model family or subscription-limit behavior changes. Do not silently
substitute another model for an unavailable reviewer or engineer; record the
blocked decision and continue only work that does not depend on it.

## Core rules

1. Use the lowest tier that is appropriate for the task's **risk**, not merely
   for its apparent size.
2. `gpt-6-sol` at high reasoning prepares architecture proposals and ADRs;
   independent `gpt-6-astra` at high reasoning accepts them before sensitive
   implementation. Astra is review-only.
3. Launch routine bounded implementation on `gpt-6-sol` at medium reasoning.
4. `gpt-6-luna` at low reasoning is restricted to mechanical work or prescribed
   checks with explicit acceptance criteria.
5. No model may approve its own result solely because it is the strongest
   available model. Tests, measurements, source evidence, and review remain
   mandatory.
6. If a required model is unavailable, continue independent
   research, tests, fixtures, documentation, and mechanical preparation. Do
   not let another tier make the blocked decision implicitly. One failed
   dispatch is enough: report it rather than retrying or silently upgrading.
7. Do not create entities beyond necessity: reuse the existing module, test,
   diagnostics and documentation before adding another abstraction or tool.
8. Verify every runtime hypothesis live before claiming it confirmed or fixed.
   Unit/static evidence remains useful but cannot replace the actual playback
   path. If a live check is unavailable, state that limitation explicitly.
9. Video Output Diagnostics and its Copy Report are the single user-facing
   place for output investigation; do not build a parallel diagnostic UI or
   a separate one-off runner when the existing module can answer the question.

## Routing matrix

| Tier | Default configuration | Use for | Do not use for |
| --- | --- | --- | --- |
| Architecture proposer | `gpt-6-sol`, `high` reasoning | Prepare ADR options, invariants, validation and rollback for renderer, synchronization, color, Wayland/GTK, Dolby Vision, dependencies and sensitive milestone failures | Implement a sensitive choice before independent acceptance; self-approve an ADR |
| Independent architecture reviewer | `gpt-6-astra`, `high` reasoning | Review one coherent proposal or sensitive changeset against source, tests and ADRs; accept or reject the ADR | Write implementation, run routine tests, formatting, summaries, or take over an unavailable engineer |
| Engineer | `gpt-6-sol`, `medium` reasoning | Bounded feature implementation, normal debugging, test design, refactoring inside an accepted architecture, integration work | Introduce a new backend contract or change an HDR invariant without accepted ADR review |
| Mechanical/check Operator | `gpt-6-luna`, `low` reasoning | Renames, manifest checksum updates, formatting, prescribed tests, small isolated fixtures | Ambiguous debugging, FFI, GPU synchronization, color science, dependency upgrades with behavioral impact |
| External test Operator | User-designated target-hardware agent (currently Antigravity / Gemini Flash on Intel/NVIDIA) | Execute one immutable validation hand-off, capture raw artifacts and environment facts | Edit code, improvise retries, change dependencies or criteria, interpret color correctness, or accept a row/gate |

No automatic model substitution or upgrade is permitted. Evidence-triggered
escalation under this policy requires a stated reason and an explicitly routed
task; it is not a fallback for a failed dispatch. If the specified route cannot
be dispatched once, report the limitation and continue independent work. A
stronger model is not a fallback implementation agent.

## What counts as architecture in this project

The following changes require a Sol high proposal and independent Astra high
ADR acceptance before implementation:

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

Escalate from Luna to Sol medium when any of these becomes true:

- the task is ambiguous or requires choosing between implementations;
- a change crosses module boundaries;
- a test failure is not explained by the task brief;
- the prescribed mechanical task needs a new engineering choice;
- the diff changes behavior rather than only representation or metadata.

Escalate from Sol medium to a Sol high proposal and Astra high review when any
of these becomes true:

- an architectural item listed above is encountered;
- two materially distinct local implementation/debugging attempts fail at the
  same boundary; stop and hand off or put the task on `HOLD`;
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

A task sent to Sol medium or Luna low must be bounded and include:

- the exact objective and files in scope;
- the already accepted architectural constraints;
- explicit non-goals;
- commands or checks that demonstrate completion;
- the conditions that require escalation rather than improvisation.

Give agents the smallest sufficient context packet: a decision summary,
relevant files, accepted ADR and focused checks, not full history. Default to
one active implementation agent plus at most one independent prescribed-check
Operator, with explicit non-overlapping file ownership. Stop after two
materially distinct local attempts at the same failure, then hand off or HOLD.
Keep command output bounded; expand only the failing segment. Do not repeat an
unchanged full suite without a material code or evidence change.

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
preflight boundary. Otherwise it is retained as an attempt. The evidence
adjudicator, not the executor, classifies the result under ADR-0006.

## Validation execution controls

- Split aggregate gates into independently decidable evidence rows. Preserve
  accepted rows when another row fails or is unavailable.
- Gate 2W is the external Wayland/compositor submission track. It cannot be
  inferred from FBO/GDK evidence and cannot force retries of those rows.
- Use at most an initial hardware run and one evidence-driven corrective run in
  an attempt family. A third requires independent Astra high review of a Sol
  high proposal, a new ADR row revision,
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
  hardware row. Prose may summarize but cannot replace them. Personal paths,
  media titles, and raw machine identifiers must be kept locally; public summaries
  and documentation must remain anonymized.

## Review and verification

- Luna changes require a diff inspection and the prescribed check. Sol medium
  can review unless an architecture trigger is present.
- Sol medium changes require relevant automated tests and a review of boundary
  assumptions. Sol high prepares sensitive proposals; independent Astra high
  reviews the ADR and coherent architecture-sensitive changeset.
- Batch one Astra review per coherent changeset. Request follow-up only for a
  material unresolved concern, never routine tests, formatting or summaries.
- GPU/color correctness claims require pixel tests or real-hardware evidence;
  model confidence and screenshots alone are insufficient.
- Performance claims require comparable measurements using the same media,
  configuration, hardware, and observation window.
- An implementation that contradicts an ADR is rejected even if its tests pass;
  either restore the decision or reopen the ADR with Sol high proposal and
  independent Astra high acceptance.

## Minimal implementation and live diagnostic evidence

- Start with the smallest change that answers the current question. Reuse
  `hdr_diagnostics.py`, its existing UI/report helpers and existing tests.
  New modules, classes, services, timers, caches, settings, scripts, schemas,
  gates or documents require a concrete reason reuse is insufficient. This
  is not permission to remove existing user work or necessary safety checks.
- Write the hypothesis, expected observation and disconfirming observation
  before a live check. Record outcomes in the existing task/review record;
  do not introduce another tracking framework. Several compatible passive
  observations may share one capture of the current session.
- A runtime fix is not verified solely because code changed or mocks passed.
  Check the real CineHDR process and affected path; show the observation in
  Video Output Diagnostics and Copy Report. Source inspection, user-reported
  improvement and instrumented live measurements must remain distinguishable.
- Keep every identified problem boundary visible: backend/fallback and loaded
  dependencies; requested/actual decoder; source/target transfer and primaries;
  FBO and published texture; GTK renderer/offload and surface evidence;
  monitor-reported versus measured luminance; Dolby Vision metadata versus
  RPU processing; frame timing/drops, A/V sync and lifecycle/probe failures.
  Show unavailable checks with their reason rather than hiding them or
  fabricating a PASS. The dialog is not a colorimeter or protocol oracle.
- UI and Copy Report must use the same collected facts, units, provenance and
  availability. An export should identify the process/build, sample time and
  measurement interval when known, and state Unknown otherwise. Preserve
  privacy: no media paths, secrets or full environment dumps.
- A configured option is not active hardware; filter/container FPS is not
  measured presentation FPS; published textures are not presented frames;
  unknown counters are not zero; absent A/V data is not synchronization.
  Cumulative counters need an interval/reset identity before rate claims.
- Keep diagnostics observational. Reuse cached/asynchronously collected state;
  do not block GTK, modify playback, switch renderers, install dependencies or
  launch stress tests on opening/refreshing the dialog. A forced test override
  must be labelled as an override, not proof of surface submission.
- Live validation does not waive ADR-0006, its two-attempt budget, explicit
  third-run approval or the external Operator boundary. Prefer a passive
  current-session report, then the shortest authorized discriminating run.
  If permissions/hardware are unavailable, retain the unverified status and
  give the user a bounded hand-off; never request /dev/dri write access.

## Budget controls

- Batch related architecture questions into one Sol high proposal and one
  independent Astra high review of the coherent changeset.
- Ask Sol for options, risks, invariants, validation and rollback; implement
  only after Astra accepts the ADR. Do not use Astra for routine execution.
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
