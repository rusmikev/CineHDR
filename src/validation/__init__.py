"""Optional validation package, installed only with -Dvalidation=true."""

from pathlib import Path

# Development checkouts keep the validation sources under tools/. Installed
# packages place them next to this file in cinehdr/validation/.
_source_validation = Path(__file__).resolve().parents[2] / "tools" / "validation"
if _source_validation.is_dir():
    __path__.append(str(_source_validation))
