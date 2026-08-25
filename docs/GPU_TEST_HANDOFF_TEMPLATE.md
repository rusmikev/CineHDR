# GPU test Operator hand-off template

Use this template for a user or external agent running CineHDR tests on target
hardware. The Architect fills it in; the Operator executes it unchanged. The
governing policy is
[ADR-0006](adr/0006-bounded-validation-governance.md).

## Operator constraints

- Do not edit the repository, test, command, environment variables, fixture, or
  acceptance criteria.
- Do not install or update software unless a separate user instruction says to.
- Execute the command at most once. Do not retry or troubleshoot in place.
- Stop at the stated timeout or stop condition.
- Return artifacts on PASS, WARN, FAIL, timeout, crash, or failed preflight.
- Do not decide whether an evidence row or gate is accepted.

## Identity and scope

- **Evidence row:** `<gate/row ID>`
- **Attempt family / attempt:** `<family revision> / <1 or 2>`
- **Hypothesis:** `<one falsifiable sentence>`
- **Exact CineHDR commit:** `<40-character commit>`
- **Expected clean checkout:** `<git status --porcelain output must be empty>`
- **Expected runtime/dependencies:** `<mpv, FFmpeg, libplacebo, driver>`
- **Target GPU and selector:** `<vendor/model plus exact environment selector>`
- **Fixture path and SHA-256:** `<path supplied locally; hash retained in report>`
- **Explicit non-claims:** `<what this run cannot prove>`

## Reason for attempt 2

Delete this section for attempt 1.

- **Prior report and SHA-256:** `<path and hash>`
- **Normalized failure signature:** `<boundary and observable that failed>`
- **Single material change:** `<implementation/input/environment change>`
- **Expected discriminator:** `<observation that must differ if the hypothesis is correct>`
- **Local regression evidence:** `<test/check that rejects the old behavior, or why impossible>`

## Preflight

Run only these read-only checks:

```sh
cd <absolute-checkout-path>
git rev-parse HEAD
git status --porcelain
<additional prescribed read-only checks>
```

If identity, cleanliness, dependency, display/session, GPU, fixture, or free
space differs from the brief, do not run the test. Return `PREFLIGHT_BLOCKED`
with the complete output.

## Single authorized execution

- **Expected duration:** `<minutes>`
- **Hard timeout:** `<minutes>`
- **Stop condition:** `<first strict failure, timeout, or normal completion>`

```sh
cd <absolute-checkout-path>
<one exact copy/paste-ready command>
```

## Required return package

Return all of the following without interpreting the gate:

- exact command as executed;
- start/end timestamps and exit code;
- complete stdout and stderr;
- paths and SHA-256 hashes of every new JSON/text/log artifact;
- `git rev-parse HEAD` and `git status --porcelain` after execution;
- requested GPU/driver/session facts;
- one of `COMPLETED`, `PREFLIGHT_BLOCKED`, `TIMED_OUT`, or `CRASHED`;
- any unexpected observation, labelled as an observation rather than a cause.

## Architect adjudication

The Operator leaves this section empty. The Architect records the row status,
evidence links, whether the attempt budget is exhausted, and the permitted next
action under ADR-0006.
