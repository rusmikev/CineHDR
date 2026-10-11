# Model Usage Policy

## Purpose and current operating profile

Updated 2026-09-25 at the user's request to preserve limited Codex capacity and
use available Gemini engineering capacity. This is the shared routing source
for `AGENTS.md`, `GEMINI.md`, reviews and hand-offs. Accepted ADRs govern
technical invariants and evidence acceptance. Model confidence is not evidence.

The current profile is **Gemini-first**. Gemini handles bounded engineering,
tests, documentation and proposal preparation. GPT-6 is used selectively where
an identified uncertainty or risk warrants it, not as a mandatory second pass.
Use the Gemini model actually available to the user; do not assume a particular
version, capability tier or unlimited allowance. Keep GNOME as the current
debugging target; KDE follows after the core path is verified on GNOME.

The user's instruction to send subsequent work to Gemini remains in force
until changed. Do not automatically launch GPT agents. An explicitly assigned
Codex task authorizes that task, not a chain of additional model calls. Existing
authorization for a defined review or hand-off persists; do not ask again at
each reversible step. A hand-off prepared here is not a completed Gemini task.

This revision changes routing, not acceptance criteria. It does not reset
attempt counts, release a HOLD, accept a pending patch, or authorize a hardware
run. Historical model assignments in usage records remain historical.

## Roles and model selection

Choose by risk, available capacity and expected total work, including rework.
These are project defaults, not claims that a model is always cheaper or better.

| Route | Default use | Boundary |
| --- | --- | --- |
| Available Gemini | Primary Engineer for implementation, debugging, tests and documentation within accepted architecture; may prepare architecture proposals | Capability must fit the task; no self-acceptance of sensitive work |
| `gpt-6-luna`, low | Mechanical changes, prescribed checks and small low-risk edits with explicit criteria | No ambiguous renderer, FFI, synchronization, color or architecture decisions |
| `gpt-6-sol`, medium | Bounded difficult implementation or debugging when routed to Codex | Respect accepted invariants and the user's budget preference |
| `gpt-6-sol`, high | Architecture proposals, conflicting evidence and substantial independent reviews | Reviewer must be independent of the author |
| `gpt-6-astra`, high | Selective review of the hardest or highest-risk unresolved questions; specifically assigned difficult engineering when justified | Do not dispatch for routine tests alone or automatic takeover; a separate reviewer must assess its sensitive implementation |
| External test Operator | User-designated target-hardware executor, including Antigravity / Gemini Flash when assigned that role | Immutable execution and raw artifacts only; not gate acceptance |

Engineer, Reviewer and Operator are task roles, not permanent restrictions on
a provider. A Reviewer assignment is review-only: report evidence and required
corrections without editing the implementation. An Engineer assignment permits
bounded implementation without another permission round. The hardware Operator
role remains separate from either engineering or acceptance in that hand-off.

No mandatory Sol -> Astra sequence applies. A Gemini proposal or patch may go
directly to an independent reviewer; do not rewrite it through Sol merely to
satisfy a model label. Astra high is preferred for exceptionally difficult
reviews, not required for every change. An appropriately capable Sol high or
available Gemini reviewer may perform the same role with the same criteria.

Independence requires a separate reviewer session or agent that did not author
the proposal or implementation. Give it the actual source/diff, accepted
invariants, raw evidence and relevant negative cases, not only the author's
summary. Prefer a different model/provider for high-risk work when available;
the same model in a fresh independent session is permitted, with that fact
recorded. Renaming the author's role or asking it to critique itself is not
independent review. No author may accept its own sensitive work.

If an explicitly selected model is unavailable, stop after one failed dispatch
and report it. Do not retry dispatch or silently substitute/upgrade. A capable
alternative within the already authorized route may be named and used; a
change outside that route needs a user decision. If no qualified independent
reviewer is available, hold the affected decision and continue independent
work. Quota pressure cannot justify self-review or a weaker acceptance oracle.

## Architecture and review triggers

The following changes require a proposal and independent ADR acceptance before
implementation, regardless of provider:

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

## Engineering and review workflow

- Routine work inside accepted architecture needs a scoped diff inspection and
  relevant checks, not a mandatory premium-model review or a new ADR.
- Sensitive architecture, render/color evidence criteria, FFI/lifetime changes,
  conflicting invariants and exceptions to the hardware budget require an
  independent reviewer. Existing accepted ADRs may be reused when the change
  implements them without changing their decision. Do not invent a new ADR for
  each bounded fix. A contradiction requires reopening the ADR before proceeding.
- Stop after two materially distinct local attempts at the same failure. Hand
  off the retained failures for a focused independent root-cause/plan review, or
  place that boundary on HOLD. Resume a bounded correction only after review
  identifies a material change and its regression check. This does not mandate
  two premium models, an architectural redesign, or a hardware rerun.
- Batch one independent review per coherent changeset. Follow up only on a
  material unresolved defect or a changed invariant; do not restart review for
  formatting, wording or unchanged tests. Use severity and concrete failing
  cases to distinguish blockers from optional improvements.
- GPU/color correctness needs an appropriate pixel or hardware oracle, and
  performance needs comparable media, configuration and measurement intervals.
  Mocks, screenshots and model summaries cannot establish broader claims.
- Do not create entities beyond necessity. Extend existing modules, tests,
  diagnostics and documents before introducing another abstraction or tool.
- Verify every runtime hypothesis live in the actual CineHDR playback path
  before calling it confirmed or fixed. Static/mocked evidence is a prerequisite;
  a missing live check stays Unverified, without erasing useful local results.
- Preserve user changes and the usable stable renderer. Do not push, publish,
  open pull requests or change remote repositories without explicit user
  authorization.

## Task hand-off and budget controls

Each task states the role, actual model if known, objective, exact files,
accepted constraints, non-goals, completion checks and stop conditions. Route
only the next useful unit of work. Supply a short context packet with relevant
source, failure evidence and decisions instead of replaying the entire chat.

Default to one active implementation agent plus at most one independent
prescribed-check Operator, with non-overlapping file ownership. Do not spawn
agents simply because slots are available. A reviewer can be a separate Gemini
session; it need not consume another Codex task. Finish the scoped task and
return the result in one batch rather than requesting permission per file.

Run focused checks first and required checks once. Do not repeat an
unchanged full suite without a material code or evidence change. Keep output
to failures, relevant evidence and the next decision. Retain immutable hardware
artifacts and review inputs, but do not regenerate reports, hashes or long
status narratives when their inputs have not changed.

When a row is on HOLD, advance an independent row or prepare the missing
discriminating evidence. Gate 2W must keep a concrete next action; it is neither
abandoned nor a reason to block accepted CineHDR-owned work. Record consequential
routing, failed attempts, review decisions and verification in the existing
`docs/MODEL_USAGE_LOG.md`; do not add another tracking service or log every read.

Observe current product limits when they affect a routing decision. Do not poll
usage after every command or invent a fixed per-task quota. API prices are not
Codex subscription usage. Model, context, reasoning, tools and task complexity
affect usage; a cheaper tier can cost more overall through rework. User budget
preferences take precedence over available quota. Do not consume reset credits,
buy capacity or change subscriptions without explicit user authorization.

## External hardware test hand-off

External test Operator is a role assigned for one execution task, not a limit
on what Gemini may do in a separate Engineer or Reviewer task. The evidence
adjudicator owns the hypothesis, criteria and interpretation. The Operator gets:

- exact commit, clean execution checkout, runtime/dependency identity, target
  GPU, fixture identity and prerequisites;
- one immutable command, one allowed execution, expected duration, hard timeout
  and stop-on-failure condition;
- required stdout/stderr, exit code, machine-readable reports, hashes and
  environment facts;
- explicit prohibitions on edits, changed arguments, automatic retries,
  dependency installation, threshold changes and gate sign-off.

Return artifacts including on failure; do not repair the test in place. A
failed preflight consumes no hardware attempt only if the application/renderer
never starts and this is recorded. The evidence adjudicator, not the executor,
classifies the result under ADR-0006.

## Validation execution controls

- Preserve independently accepted rows when another row fails or is unavailable.
  Gate 2W is the external Wayland/compositor submission track; FBO/GDK evidence
  cannot establish it or be invalidated merely by its remaining OPEN.
- Fixed numerical attempt limits are removed. Every new real-hardware execution
  requires a concrete hypothesis, a material difference from the prior run, an
  expected discriminating signal, and preservation of raw artifacts. Unchanged
  repeats without diagnostic purpose are strictly prohibited.
  Put the evidence row on `HOLD` when a fundamental defect requires architecture review.
  Policy/model changes do not reset hardware budgets, release existing HOLD rows, waive preflight,
  or authorize new runs. Failure-message, timeout or logging changes alone do not
  constitute a material change.
- A rerun brief names the failure signature, single material change, expected
  discriminating evidence, command, timeout and artifacts. Never repeat an
  unchanged soak or hardware command. Use a soak only for a duration hypothesis.
- Use unit/static, compile and software/offscreen evidence before hardware.
  Keep an oracle's claim narrow: context creation is not frame correctness.
- Only sanitized machine-readable reports with exact provenance can close a
  hardware row. Keep personal paths, media titles and raw machine identifiers
  locally; public summaries and documentation must remain anonymized.

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
- Live validation does not waive ADR-0006, discriminating run criteria, explicit
  user run approval or the external Operator boundary. Prefer a passive
  current-session report, then the shortest authorized discriminating run.
  If permissions/hardware are unavailable, retain the unverified status and
  give the user a bounded hand-off; never request /dev/dri write access.

## Sources and maintenance

Routing choices above are project decisions. The official GPT-6 model catalogue
describes Astra as the frontier model, Sol as a balanced workhorse and Luna as
the efficient tier; this does not establish an account's availability or quota.
Consult the actual model picker/dispatch metadata for supported configurations.

Sources checked 2026-09-25:

- [OpenAI model catalogue](https://developers.openai.com/api/docs/models)
- [Codex usage and pricing](https://learn.chatgpt.com/docs/pricing)

Review routing when access, measured rework or budget preferences change. Do
not hard-code subscription quotas, API prices or unverified Gemini model names.
Keep the evidence and role-separation requirements when updating these routes.
