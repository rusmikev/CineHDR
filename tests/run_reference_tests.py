#!/usr/bin/env python3
"""Compatibility entry point for the accepted Gate 2 FBO reference harness.

The former placeholder deliberately succeeded without evidence.  Reference
validation is now the explicit, strict ADR-0004 runner; missing dependencies
or measurements return a non-zero status and a JSON failure report.
"""

from __future__ import annotations

from run_pixel_pipeline import main


if __name__ == "__main__":
    raise SystemExit(main())
