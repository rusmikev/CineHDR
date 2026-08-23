# ADR-0002: User-facing GPU Next selection with restart boundary

- **Status:** Accepted
- **Date:** 2026-08-20
- **Scope:** Local `gpu-next-local` development line
- **Builds on:** [ADR-0001](0001-gpu-next-render-api.md)

## Context

The experimental `opengl-next` Render API now produces SDR and HDR first
frames inside CineHDR's existing GTK surface. It is useful to let local users
select it without learning environment variables, but it is not safe to swap
render contexts while a process is running. A saved preference also needs to
remain recoverable on installations that still load an unpatched libmpv.

## Decision

Add a persistent `render-backend` GSettings enum with values `legacy` and
`gpu-next`, defaulting to `legacy`. CineApplication captures one immutable
process selection at startup and passes it through every window to the main
video widget.

Selection priority and fallback are:

1. `CINEHDR_RENDER_BACKEND` overrides GSettings for development.
2. Explicit environment `gpu-next` is strict and fails visibly if unavailable.
3. Environment `auto` tries `opengl-next`, then `opengl`, only while creating
   the context.
4. Saved `gpu-next` tries `opengl-next`, then `opengl`, only while creating the
   context. The saved preference is preserved so a later compatible build can
   use it.
5. Saved or explicit `legacy` uses `opengl` directly.

Changing the UI setting saves the next-launch choice only. It never replaces
the renderer in a running process. The UI compares the current saved choice to
the renderer requested for this session and shows a restart requirement when
they differ.

The playback menu becomes **HDR & Video Output** because renderer selection
applies to HDR and SDR content. The same persistent switch is available in
the always-accessible **Preferences → Video** group, including on the start
page where playback controls are intentionally hidden. HDR reset controls do
not modify the renderer preference.

When GSettings supplied the process-start selection, changing the saved value
shows a restart requirement until the user reverts it. A developer environment
override instead shows that the override is active; restarting with the same
override cannot apply the saved preference.

## Diagnostics and capability claims

Diagnostics distinguish:

- the saved choice for the next normal launch;
- the immutable request and its source for this process;
- the actually active libmpv API;
- startup fallback or restart-required reasons;
- initialization, runtime, and GL-lifecycle failure states;
- loaded dependency identity and current render-target format/depth.

An active `opengl-next` context proves only that the experimental API is
available in this session. It does not prove production packaging support or
Dolby Vision correctness. Dolby Vision Profile 5 remains blocked until the
ADR-0001 validation gate passes.

## Consequences

- Stable installations remain launchable after selecting GPU Next because the
  saved preference has a creation-only legacy fallback.
- An environment override remains suitable for strict developer testing.
- Multiple windows use the same process selection and cannot diverge.
- One startup fallback notification is shown per process; full details remain
  available in diagnostics.
- A failed renderer is reported as stopped and still requires a process
  restart. GLArea recreation cannot clear this terminal failure latch;
  diagnostics never trigger a live backend swap.

## Rollback

Turn off GPU Next in **HDR & Video Output** and restart CineHDR. The setting has
no data migration and does not alter media, playlists, or HDR preferences.
