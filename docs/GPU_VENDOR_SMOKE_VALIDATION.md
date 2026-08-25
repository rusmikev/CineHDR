# Intel/NVIDIA vendor smoke validation

- **Harness revision:** `cinehdr-vendor-smoke-v2`
- **Implementation status:** locally verified; real hardware `NOT_RUN`
- **Governance:** [ADR-0006](adr/0006-bounded-validation-governance.md)
- **Executor template:** [GPU test hand-off](GPU_TEST_HANDOFF_TEMPLATE.md)

## Evidence boundary

This smoke row answers one narrow question: on one selected GPU, can the exact
pinned CineHDR Flatpak keep the strict legacy or GPU Next process alive for an
eight-second observation while rendering the prescribed local HDR fixture?

A process result is `PASS` only when all of these observations exist together:

- the requested renderer mode, exact active `opengl` or `opengl-next` API, and
  `fallback_reason=none`;
- mpv `v0.41.0-dev-g97179bce7`, libplacebo `v7.360.1`, and FFmpeg `8.1.2`;
- the expected Intel or NVIDIA GL vendor and laptop renderer identity;
- an ordinary Render API target frame;
- a separate loaded-media-frame marker with positive source dimensions,
  non-negative `time-pos`, and a SHA-256 token of the exact canonical media
  path;
- matching fixture SHA-256 and HDR telemetry;
- no file-open, application, renderer, or unexpected process-exit error.

The marker is deliberately stronger than the historical `Rendered first
frame` line: an initial empty libmpv render cannot satisfy it. The JSON report
omits absolute media paths and records raw-log hashes, exact source and Flatpak
commits, explicit non-claims, and `gate_adjudication=NOT_PERFORMED_BY_OPERATOR`.

This row does not prove pixel/color correctness, Gate 2W compositor behavior,
display output, Dolby Vision certification, performance, or longer stability.

## Bounded execution shape

One command targets exactly one GPU family, at most two renderer processes,
and at most two independently named fixture rows. Missing or changed fixtures,
a dirty/wrong checkout, a stale Flatpak commit, or a non-Wayland session stop at
`PREFLIGHT_BLOCKED` before CineHDR starts. No fixture is silently skipped.

The first vendor baseline should select only `hdr10` and the two renderer
configs for that GPU. That is two eight-second processes. HLG, Dolby Vision
Profile 8, and the second PQ fixture remain separate evidence rows and are not
bundled into the first smoke merely to recreate the historical 16-process
matrix.

Both Intel and NVIDIA use the same installed pinned Flatpak. NVIDIA selection
adds the explicit PRIME environment, while the runtime GL identity remains the
acceptance observable. No native custom stack is built by the user.

## Command shape

The Architect replaces every placeholder after the relevant source revision is
committed and the corresponding Flatpak candidate is built. A command that
still contains a placeholder is not authorized for execution.

Intel baseline:

```sh
python3 tests/test_nvidia.py \
  --config intel-legacy --config intel-gpu-next \
  --fixture hdr10 \
  --fixture-root <absolute-haasn-fixture-root> \
  --expected-git-commit <40-character-source-commit> \
  --expected-flatpak-commit <installed-ostree-commit> \
  --output /tmp/cinehdr-intel-smoke-v2.json
```

NVIDIA baseline:

```sh
python3 tests/test_nvidia.py \
  --config nvidia-legacy --config nvidia-gpu-next \
  --fixture hdr10 \
  --fixture-root <absolute-haasn-fixture-root> \
  --expected-git-commit <40-character-source-commit> \
  --expected-flatpak-commit <installed-ostree-commit> \
  --output /tmp/cinehdr-nvidia-smoke-v2.json
```

The user-designated Antigravity/Gemini Flash 7 agent receives only one filled
command at a time and follows `GPU_TEST_HANDOFF_TEMPLATE.md`. It returns the
JSON and sibling artifact directory even on failure and does not retry, modify
arguments, diagnose, or close the row.

## Current hold point

The v2 implementation has passed local source tests and can be packaged as a
clean source commit plus a matching custom Flatpak. Real Intel/NVIDIA hardware
status remains `NOT_RUN`. A hardware command is authorized only through a
separate immutable hand-off in which the Architect has filled the exact source
commit, installed Flatpak commit, fixture path and hash, attempt identity, and
timeout. Installing or updating that candidate remains a separate
user-authorized setup action and is not delegated implicitly to the Operator.
