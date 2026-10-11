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

### Hardware execution governance

Fixed numerical attempt limits per family are removed by user decision (2026-10-10).
Instead, hardware execution is governed by falsifiable hypotheses and discriminating evidence:

1. Every new real-hardware execution requires a concrete hypothesis,
   a material difference from the previous run, an expected discriminating signal,
   and preservation of raw artifacts.
2. Unchanged repeats without diagnostic purpose are strictly prohibited. A rerun brief must name
   the prior failure signature, the single material change, the expected discriminating evidence,
   exact command, timeout, and required artifacts.
3. After a failure, no new hardware command is issued until the failure is classified and the
   implicated behavior has the strongest available local unit, static, compile, or software reproduction.
4. When a fundamental defect or design conflict requires architectural rework, the row is placed
   on `HOLD`. A new hardware run requires independent architecture review by a qualified separate reviewer
   under `docs/MODEL_POLICY.md`, retained failures, an ADR/row revision, regression check, execution brief,
   and explicit user approval.
5. Preflight checks, user instance protection, and evidence standards remain mandatory and unweakened.
   Editing governance rules or model policies does not release existing HOLD rows or authorize hardware runs.

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

A user-designated agent on target hardware is an Operator for its assigned
execution hand-off under `docs/MODEL_POLICY.md`. This includes Gemini when
assigned that role; it may perform separate Engineer or Reviewer tasks, but
may not combine those roles with this execution. The Operator may:

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
- Hypothesis-driven governance avoids arbitrary numerical cutoffs while preventing
  unbounded iteration by strictly requiring discriminating evidence and material differences.
- External test capacity increases hardware coverage without transferring
  architecture or acceptance authority to the execution role.

## Measurable validation

- `AGENTS.md` and `docs/MODEL_POLICY.md` contain the hypothesis governance rule and the
  external Operator boundary.
- Every future hardware run request links retained reports, an architecture review,
  a new row revision, and explicit user approval when returning from HOLD.
- Every future hardware hand-off contains all rerun-brief fields above.
- Gate summaries link machine-readable row reports; a report-absent row cannot
  be `PASS`.
- Gate 2W remains separately reported and cannot block retries of accepted
  CineHDR-owned rows.
- No duration test is repeated without a changed duration-specific hypothesis.

## Routing amendment (2026-09-25)

At the user's request, reviewer eligibility now follows MODEL_POLICY's role
and capability rules instead of requiring Sol authorship and Astra review by
name. Independent review, both retained failures, the regression check, row
revision and explicit third-run user approval remain mandatory. This amendment
does not reset attempt counts, release existing HOLD rows, accept pending
patches or authorize another hardware execution. It changes no render invariant
or evidence oracle. Governance checks cover the updated entry points and these
retained requirements; no GPU execution is needed to validate this policy edit.
If the routing change proves inadequate, tighten reviewer eligibility while
retaining all attempt history and evidence requirements.

## Gate 2W multi-phase provenance and execution amendment (2026-10-04)

Following independent review of Gate 2W Attempt 4 preparation, this amendment
establishes the provenance, phase-correlation, backend separation, and cleanup
governance for Gate 2W validation on GNOME Shell 50.5 / Wayland:

1. **Separation of product code and deployment identity**:
   - Product code (`src/gpu_validation.py`) validates runtime dependency identity
     strictly in its execution context using exact canonical paths:
     - In verified Flatpak container context, CineHDR confirms the execution
       environment via multiple internal facts:
       1. readable and parseable `/.flatpak-info` matching `[Application] name=io.github.rusmikev.CineHDR`;
       2. read-only mount of `/app`;
       3. exact canonical path of `libmpv` within `/app/lib/` (resolving via
          `os.path.realpath` to `/app/lib/libmpv.so.2.5.0` or symlink `/app/lib/libmpv.so.2`),
          strictly rejecting `/usr/lib`, `/tmp`, home directories, or foreign paths.
       Merely checking a boolean `is_flatpak()` flag or environment variable is
       insufficient to assert a verified container context.
     - On host development checkouts, `libmpv_path` must resolve canonically
       to the sibling prefix directory:
       `repo_root.parent / "mpv-gpu-next-prefix" / "lib64" / "libmpv.so.2.5.0"`
       (or symlink `libmpv.so.2`), matching the canonical development build command
       `meson setup build --prefix="$(pwd)/../mpv-gpu-next-prefix"`.
       Loose suffix matching (`.endswith()`) is prohibited to prevent path spoofing.
   - Pinned deployment identity (exact OSTree `app-commit` and `runtime-commit`
     from `org.gnome.Platform//50`) is enforced externally by the validation
     launcher (`run_wayland_publication_validation.sh`) via container metadata
     (`$XDG_RUNTIME_DIR/.flatpak/<instance-id>/info`).
     Future Candidate v5 commit hashes must not be baked into the application's
     own source code.

2. **Phase-by-phase Wayland color-management protocol correlation & composite window path**:
   - Simple counting of `set_image_description` events is prohibited.
   - Observation from live trace `wayland-trace-Z4GghM`: on GNOME Shell 50.5 (Mutter)
     with `org.gnome.Platform//50` (GTK 4.16+), `wp_color_management_surface_v1` is bound
     to the toplevel window `wl_surface`, where buffer attaches and commits occur, while
     subsurface `wl_surface#71` receives no video buffer attachments. This reflects GTK4's
     actual composite window path where GLArea rendering is composited into the
     window buffer. This trace observation documents the composite window path for
     this environment; it does not assert that all other GTK/GNOME paths (such as
     direct subsurface scanout via `GtkGraphicsOffload`) are universally impossible.
   - Dynamic window surface identification: The protocol parser does NOT hardcode a
     static surface number (such as `wl_surface#61`). Instead, it dynamically resolves
     CineHDR's window surface from the Wayland trace by following the creation chain:
     `xdg_wm_base.get_xdg_surface(..., wl_surface#X)` ->
     `xdg_surface.get_toplevel(...)` ->
     `xdg_toplevel.set_app_id("io.github.rusmikev.CineHDR")`.
     The dynamically resolved `wl_surface#X` is tracked for color-management binding
     and buffer commits.
   - Reconciliation with ADR-0004 and Row Separation:
     - **Primary Row**: **"Client-side color-management submission for composite window"**
       (within Gate 2W).
       In strict accordance with binding ADR-0004 (lines 311–318):
       - *GDK Texture Publication*: Requires all 4 distinct publication states in the
         CineHDR report (`gate2-wayland-publication-${backend}-*.txt`), proving alternating
         SDR (`GL_RGBA8`/8-bit) -> HDR (`GL_RGBA16F`/16-bit) -> SDR -> HDR transitions
         with active renderer API `opengl` or `opengl-next`.
       - *Wayland Protocol Submission*: Requires the original ADR-0004 oracle on the
         dynamically identified CineHDR window surface:
         `ready` (or `ready2`) -> `set_image_description` (with perceptual intent) -> `commit`
         in each backend execution without protocol errors.
         As ADR-0004 establishes, the submitted surface description is not required
         to alternate between sRGB and PQ on the wire, because GTK may legally transform
         both input texture states into a single compositor-preferred surface encoding
         (e.g. wide-gamut / Rec.2100 PQ or scRGB linear) without reconfiguring surface
         metadata on every media switch.
     - **Stricter Extension Row**: **"Strict multi-phase Wayland color description alternation"**
       (separated exploratory sub-row).
       - Evaluates whether GTK dynamically reconfigures the Wayland window surface
         between explicit sRGB (`prim=1, tf=9`) and Rec.2100 PQ (`prim=6, tf=11`)
         across the 4 phase transitions.
       - *Justification for separation*: Elevating 4-phase surface description alternation
         to a mandatory condition for the primary row would contradict ADR-0004 and
         falsely reject compliant GTK implementations that maintain a single wide-gamut
         composited surface format while correctly converting textures. Keeping this
         as an independent extension row permits deeper characterization without
         breaking ADR-0004 consistency.
   - Distinct provable claims:
     1. *GDK Texture Publication*: Four distinct publication states in the CineHDR
        report, verifying format, dimensions, color-state equality, and active API.
     2. *Wayland Protocol Submission*: Valid color management binding and submission
        on the dynamically resolved CineHDR window surface in each backend process.
     3. *Container Instance & Session Provenance (`container_session_correlated: true`, `same_process_wire_correlated: false`)*:
        Both the GDK publication report and the Wayland debug log originate from the
        verified container instance and identical execution session, strictly verified via:
        - `Session ID`, `Flatpak instance ID`, and `Process ID` recorded in the publication
          report header and verified in `evaluate_session_results`;
        - Container metadata (`$XDG_RUNTIME_DIR/.flatpak/<id>/info`) matching application
          name `io.github.rusmikev.CineHDR`, full 64-character `app-commit` and `runtime-commit`;
        - Process exit code 0 and runner output redirection (`>wayland-client.raw.log 2>&1`).
        - *Proof boundary*: Provenance proves identical session ID, container instance ID,
          and runner process stream redirection. The libwayland wire protocol itself does not
          transmit or embed client process PIDs or cryptographic session nonces in protocol
          messages. Consequently, claiming in-protocol wire-level process correlation is
          unproven and explicitly disclaimed (`same_process_wire_correlated: false`).
   - Boundaries strictly kept `OPEN` (non-claims):
     - *Per-frame GDK-to-commit correlation*: GTK4 currently provides no shared monotonic
       timestamp, frame sequence counter, or protocol marker tying an internal
       `GdkGLTexture` publication generation to a specific `wl_surface.commit`. A client-side
       trace cannot instrumentally prove per-frame lockstep synchronization; this sub-row
       explicitly remains `OPEN` (never claimed as `PASS`).
     - *Compositor Presentation*: Client protocol requests prove what the client submitted
       to the Wayland socket, not how Mutter composited the window buffer (e.g. blending,
       tone mapping, or pass-through).
     - *Physical Display Output*: True photon emission and luminance on the physical panel
       require physical colorimeter/photometer measurement or display telemetry.
   - Narrow row criterion and non-weakening:
     - The narrow row certifies client-side color-management submission for the composite
       window without protocol rejection.
     - Previously accepted rows are not weakened: Attempt 3's narrow first-frame row remains
       accepted for its single-frame scope; subsurface video chain criteria remain defined
       for direct-scanout offload configurations; Gate 2W aggregate gate remains `OPEN`
       (`PARTIAL_NOT_CLOSED`).
   - Negative controls:
     - *Primary Row Negative Controls* (`validate_composite_window_trace` / `evaluate_session_results`):
       - Registry omission: `wp_color_manager_v1` is not advertised by the compositor.
       - Missing capabilities: Manager burst lacks perceptual rendering intent (`supported_intent(0)`) or omits `done()`.
       - Wrong application or surface: Color management bound to an unrelated surface or foreign app window rather than CineHDR's toplevel window.
       - Order inversion: `set_image_description` called before the image description receives a `ready` or `ready2` event.
       - Missing commit: No subsequent `wl_surface.commit` observed on the toplevel window surface after `set_image_description`.
       - Protocol and fatal errors: Any `wl_display.error`, descriptor `failed` event, or compositor disconnection.
       - Publication report failures: Incomplete publication states (< 4), wrong expected backend, wrong active renderer API, or report `FAIL`.
       - Provenance failures: Mismatched session ID, unconfirmed Flatpak instance ID, mismatched container metadata (app name, full 64-char app/runtime commit), or unconfirmed instance termination.
       - **Explicit Primary Row Invariant**: An unchanging / constant surface color descriptor across transitions (e.g., constant Rec.2100 PQ or scRGB linear) does **NOT** fail the primary row, as GTK is permitted by ADR-0004 to maintain a uniform wide composited window surface format while transforming alternating GDK textures.
     - *Stricter Extension Row Negative Controls* (`validate_multiphase_trace`):
       - Alternation failure: Rejection of identical / unchanging surface descriptors across phase transitions (requires 4 distinct surface description changes).
       - Transfer function mismatch: Rejection of incorrect transfer function in an HDR phase (`tf != 11`).
       - Sequence disorder: Rejection of non-alternating phases (e.g., SDR -> SDR or HDR -> HDR).
     - Rejection of SDR manifest with HDR trace or mismatched test fixtures.
     - Rejection of missing `cleanup_status.json` or unconfirmed instance termination.
     - Rejection of unconfirmed test instance termination or `flatpak ps` query failure.

3. **Backend and aggregate gate separation**:
   - An execution of a single backend (e.g. `--backend legacy`) evaluates and records
     only its respective backend row (`legacy/session_facts.json`).
   - Gate 2W as an aggregate gate remains `OPEN` (status `PARTIAL_NOT_CLOSED`) and cannot
     be declared closed or successful without passing, container_session_correlated reports
     (`container_session_correlated: true`, `same_process_wire_correlated: false`, without
     claiming wire-level PID transmission) for both `legacy` and `gpu-next` under the same
     target environment.

4. **Guaranteed test instance cleanup and user process isolation**:
   - Pre-existing user CineHDR instances are cataloged at launcher start into an
     immutable protected set and strictly protected against signaling or termination.
   - A dedicated exit/signal trap (`EXIT`, `INT`, `TERM`, `HUP`) terminates only the
     verified, numeric test instance ID, confirmed via `$XDG_RUNTIME_DIR/.flatpak/<id>/info`
     belonging to the spawned test process.
   - In case of early failures, timeouts, or interrupts, the trap issues
     `flatpak kill <test-instance-id>` (or signals the confirmed numeric test PID),
     verifies absence in `flatpak ps`, and records teardown telemetry (trigger,
     instance ID, protected instances, outcome) into `cleanup_status.json`.
   - If `cleanup_status.json` is missing, indicates `instance_terminated: false`, or
     records status other than `CLEAN`, `evaluate_session_results` strictly fails closed (`FAIL`).
   - If `flatpak ps` fails during cleanup or instance polling, the launcher fails closed
     and refuses to assert clean instance termination.

5. **Promotion and execution sequence (Why Run 4 cannot be executed before ADR acceptance)**:
   - Candidate v4 is exhausted under ADR-0006's two-attempt family budget; Attempt 3 was
     an exceptional user-authorized run closing only a narrow first-frame row.
   - Candidate v4 contains the legacy path check that rejects container path `/app/lib/`.
   - The multiphase parser oracle currently classifies toplevel traces as `UNKNOWN`,
     because the previously accepted contract required subsurface video buffer chaining.
   - Executing Run 4 on hardware before this ADR revision is accepted by an independent
     Reviewer would violate ADR-0006 governance by running against an unapproved evidence
     oracle and an unbuilt Candidate v5.
   - The promotion sequence is strictly:
     1. Independent ADR acceptance of this governance decision and oracle specification.
     2. Implementation of exact path validation and container context checks
        in `src/gpu_validation.py` (offline, verified by unit tests).
     3. Implementation and offline unit testing of the primary composite window oracle
        (`validate_composite_window_trace` in `tests/parse_wayland_color_trace.py`) and
        its integration into `evaluate_session_results`, including positive tests for
        Attempt 3 composite path and negative tests for wrong surface, order inversion,
        missing manager capabilities, and protocol errors.
     4. Independent code and test review of the composite window oracle implementation
        and test coverage.
     5. Execution of local negative and positive regression tests.
     6. Build and installation of Candidate v5 (yielding new `app-commit`).
     7. Pinning the exact Candidate v5 commit SHA in launcher and brief.
     8. Separate, explicit user authorization under ADR-0006 for hardware Run 4.

## Publication test override amendment (2026-10-04)

Run 5 exposed a deterministic conflict in the bounded SDR→HDR publication
matrix: validation temporarily selects `auto`, while the product's conservative
auto policy stays SDR until surface submission is proven. The launcher omitted
the existing `CINEHDR_FORCE_SURFACE_SUBMISSION=1` test override. The separate
mpv `end-file` error remains unexplained.

The accepted correction passes that override only through `flatpak run --env`
to the isolated `publication` child. Application validation rejects a missing
or non-`1` value before creating the window. The report records the active
override, and the external evaluator requires that disclosure. This is test
setup for observing the four GDK texture states; it is **not** proof of normal
`auto` playback or Wayland submission. Monitor/capability and Dolby Vision
refusals, actual texture checks, the independent Wayland protocol oracle,
instance identity, and cleanup remain mandatory.

Alternatives considered: changing product `auto` policy would weaken an
unverified color-output invariant; using `force-hdr` would fail to test an
SDR→HDR→SDR transition. Both are rejected. The override risks a conditional
PASS being mistaken for normal playback behavior; the explicit report scope
and independent protocol result keep that limit visible. Offline acceptance
requires negative tests for absent/wrong override and adverse capability/DoVi/
monitor states, plus positive SDR/HDR transition and unchanged negative GDK/
Wayland oracles. Rollback removes the child argument and its report/preflight
contract without changing saved settings or product policy. This amendment
does not release the row from HOLD or authorize another hardware execution.

## Dynamic hypothesis governance amendment (2026-10-10)

Fixed numerical attempt limits per family are removed in favor of hypothesis-driven governance:
every real-hardware execution must be justified by a concrete falsifiable hypothesis, a material
difference from prior runs, an expected discriminating signal, and retention of all raw artifacts.
Unchanged retries without diagnostic purpose are strictly prohibited. Mandatory preflight checks,
user instance protection, and evidence standards remain preserved. This amendment does not release
existing HOLD rows (including the 4-state publication row) or authorize hardware runs without an
explicit user authorization and accepted execution brief.

## Rollback

This ADR changes validation governance, not playback. If the attempt budget is
too restrictive, amend the numeric budget with recorded evidence about missed
defects and execution cost. Do not remove role separation, retained failures,
claim-to-oracle mapping, or Gate 2W independence merely to obtain a green gate.
