# ADR-0006: Bounded validation governance and external test execution

- **Status:** Accepted
- **Date:** 2026-08-25
- **Scope:** GPU Next hardware, compositor, performance, soak, and external-agent
  validation
- **Builds on:** [ADR-0001](0001-gpu-next-render-api.md) and
  [ADR-0004](0004-gate2-fbo-pixel-validation.md)

## Context

The original Gate 2 combined renderer pixels, decoding, GTK publication,
Wayland surface submission, workflows, performance, and resource settling.
Several of those layers require different machines or depend on toolkit and
compositor behavior outside CineHDR's control. Treating the aggregate gate as
one blocking test encouraged an edit -> user-run -> fail loop even when a
failure had already isolated one integration boundary.

Gate 2W separated Wayland compositor surface submission from CineHDR's FBO and
GDK publication boundary, but the project rules still did not limit the total
number of real-hardware attempts. They also did not distinguish an execution
operator from the Architect who interprets evidence. A remote laptop with
Intel and NVIDIA GPUs is now available through a user-designated external test
agent, so that distinction must be explicit before further runs.

## Terms

- An **evidence row** is one independently decidable claim on one defined
  boundary, such as embedded-FBO structure, active `vaapi-copy`, GDK texture
  publication, Gate 2W protocol submission, performance, or resource settling.
- An **attempt family** starts with the first real-hardware execution of one row
  and includes corrections intended to make that same row observable. A renamed
  report schema or a different failure message does not reset the family.
- A **material change** changes the implementation, dependency, fixture,
  environment capability, or measurement method implicated by the evidence. A
  timeout increase, log wording change, or repeated command is not material.
- A **test executor** runs a prescribed hand-off and captures artifacts. An
  **evidence adjudicator** reviews those artifacts against the ADR and decides
  the row status. These roles must not be conflated.

## Decision

### Independent rows and statuses

Validation is tracked per evidence row. The allowed row statuses are:

- `NOT_RUN`: no admissible execution exists;
- `PASS`: every declared observable and provenance check passed;
- `WARN`: structural criteria passed but a declared non-gating observation
  remains unresolved;
- `FAIL`: the tested revision contradicted a criterion;
- `UNAVAILABLE`: an external capability required by the row is absent;
- `HOLD`: the execution budget is exhausted pending architecture review;
- `SUPERSEDED`: a later revision replaces, but does not erase, the report.

A gate roll-up is separate from row status. Only the evidence adjudicator may
mark a gate `CLOSED`, `CONDITIONAL`, `OPEN`, or `BLOCKED_EXTERNAL`, and only from
retained row reports. Prose summaries cannot substitute for those reports.

Gate 2 covers boundaries controlled by CineHDR through publication of the
correct GDK texture. Gate 2W is an independent Wayland compositor surface-
submission track. `UNAVAILABLE` or `BLOCKED_EXTERNAL` Gate 2W evidence does not
invalidate an accepted CineHDR-owned row and must not trigger renderer retries.
It also must not be described as compositor passthrough or display validation.

### Hardware execution budget

One attempt family has a default budget of two real-hardware executions:

1. the initial run;
2. one repeat after an evidence review identifies a single material change.

After the first failure, no new hardware command is issued until the failure is
classified and the implicated behavior has the strongest available local unit,
static, compile, or software reproduction. After a second unsuccessful
execution, the row becomes `HOLD`. Work continues on independent rows instead
of iterating on the target machine.

A third execution is exceptional. It requires all of the following before the
command is sent:

- an independent architecture review (`gpt-6-astra`, high) of the Sol high
  proposal and both retained failures;
- an ADR amendment or new row revision describing the material change;
- a local regression check that would have rejected the previous implementation
  when such a check is technically possible;
- a written execution brief with one falsifiable hypothesis;
- explicit user approval for that third run.

Changing the failure signature without changing the tested boundary does not
reset the two-run budget. A genuinely new dependency/toolkit version or a
redesigned measurement boundary may start a new family after independent
architecture review under `docs/MODEL_POLICY.md`.

### Test ladder and duration

Run the cheapest layer capable of rejecting the hypothesis first:

1. static/unit/schema checks;
2. compile or software/offscreen integration;
3. short hardware smoke, normally at most two minutes;
4. bounded hardware workflow, normally at most ten minutes;
5. a long run only when elapsed duration is itself the unique observable.

A soak is never a startup, wiring, resize-callback, logging, or fixture-debugging
tool. A completed long run is not repeated on an unchanged stack and mode. Any
new run longer than ten minutes requires a passed short preflight and a brief
explaining what shorter evidence cannot establish.

### Rerun brief

Every requested real-hardware repeat records, before execution:

- row ID and attempt number;
- exact CineHDR commit and dependency/runtime identity;
- prior report path/hash and normalized failure signature;
- the one material change since that report;
- the expected observation that distinguishes the hypothesis;
- exact command, prerequisites, expected duration, timeout, and stop condition;
- required stdout/stderr, JSON/log reports, exit code, and artifact hashes;
- explicit non-claims and the next action for both PASS and FAIL.

The command must be copy/paste-ready and non-destructive. The executor must not
have to infer media, GPU selection, environment variables, success criteria, or
where artifacts are written.

### External test executor

A user-designated agent on target hardware, including Antigravity with Gemini
Flash on the Intel/NVIDIA laptop, is an Operator under
`docs/MODEL_POLICY.md`. It may:

- verify the exact commit and clean execution checkout;
- verify listed prerequisites without changing system policy;
- execute the exact hand-off once;
- stop at the declared timeout or failure condition;
- return raw command output, exit status, environment facts, report paths, and
  hashes;
- note an unexpected observation without diagnosing or fixing it.

It must not edit CineHDR, install or replace drivers/dependencies unless the
user separately authorizes that action, retry with changed arguments, weaken a
validator, select a new threshold, discard a failed report, or declare a row or
gate accepted. The primary evidence adjudicator reviews the returned evidence. An
executor-produced summary is context, not acceptance authority.

### Claim-to-oracle rule

Each PASS claim must name an observable that actually proves it and have a
negative test where practical. Examples:

- a PNG header and dimensions prove file creation, not correct pixels, tone
  mapping, subtitles, or hardware decode;
- a backend initialization log proves context creation, not a rendered frame;
- a mocked capability flag proves controller policy, not Dolby Vision color;
- a short offscreen render loop is a microbenchmark, not ten-minute presented-
  frame performance;
- a GDK color state proves the client texture tag, not Gate 2W compositor
  submission or photons on the display.

Machine-readable evidence must be sanitized of personal media paths and must
record the exact code/dependency revision. Missing evidence is `NOT_RUN` or
`FAIL`, never an inferred PASS.

## Consequences and risks

- Some aggregate gates remain open longer, but accepted independent evidence is
  preserved and development can move to another row instead of looping.
- `UNAVAILABLE` becomes a useful result for external GTK/compositor boundaries
  rather than a reason to modify the renderer.
- The two-run budget may delay a valid third fix. Explicit Architect and user
  review is accepted as the cost of preventing unbounded hardware iteration.
- External test capacity increases vendor coverage without transferring
  architecture or acceptance authority to an inexpensive execution model.

## Measurable validation

- `AGENTS.md` and `docs/MODEL_POLICY.md` contain the two-run `HOLD` rule and the
  external Operator boundary.
- Every future third-run request links two retained reports, an architecture
  review, a new row revision, and explicit user approval.
- Every future hardware hand-off contains all rerun-brief fields above.
- Gate summaries link machine-readable row reports; a report-absent row cannot
  be `PASS`.
- Gate 2W remains separately reported and cannot block retries of accepted
  CineHDR-owned rows.
- No duration test is repeated without a changed duration-specific hypothesis.

## Rollback

This ADR changes validation governance, not playback. If the attempt budget is
too restrictive, amend the numeric budget with recorded evidence about missed
defects and execution cost. Do not remove role separation, retained failures,
claim-to-oracle mapping, or Gate 2W independence merely to obtain a green gate.
