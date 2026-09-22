from typing import Mapping, MutableMapping, Optional, Sequence, Tuple
from dataclasses import dataclass
import os
import re

INTENT_PERCEPTUAL = 0
FEAT_PARAMETRIC = 1
FEAT_SET_PRIMARIES = 2
PRIM_SRGB = 1
PRIM_BT2020 = 6
TF_EXT_LINEAR = 5
TF_SRGB = 9
TF_PQ = 11
TF_COMPOUND_POWER_2_4 = 14

@dataclass(frozen=True)
class CmCaps:
    intents: frozenset
    features: frozenset
    tfs: frozenset
    primaries: frozenset

# GDK_DEBUG parsing mirrors gdk_parse_debug_var() (gdk/gdk.c): tokens are split
# on any of ":;, \t", matched case-insensitively, and "all" *inverts* the set,
# so "all:color-mgmt" means every flag EXCEPT color-mgmt.
_GDK_DEBUG_SEPARATORS = re.compile(r"[:;, \t]")
_GDK_COLOR_MGMT = "color-mgmt"

COLOR_MGMT_OPT_IN_ENV = "CINEHDR_EXPERIMENTAL_COLOR_MGMT"
COLOR_MGMT_OPT_IN_FLAG = "--experimental-color-mgmt"

# apply_color_mgmt_opt_in() results
OPT_IN_NOT_REQUESTED = "not-requested"
OPT_IN_ALREADY_ENABLED = "already-enabled"
OPT_IN_APPLIED = "applied"
OPT_IN_TOO_LATE = "too-late"


def _gdk_debug_tokens(value: str) -> list:
    return [t for t in _GDK_DEBUG_SEPARATORS.split(value or "") if t]


def gdk_debug_enables_color_mgmt(value: str) -> bool:
    """True if GDK would enable color management for this GDK_DEBUG value."""
    tokens = {t.lower() for t in _gdk_debug_tokens(value)}
    return (_GDK_COLOR_MGMT in tokens) != ("all" in tokens)


def gtk_cm_opted_in(env=os.environ) -> bool:
    # Only truthful if GDK_DEBUG has not been changed after Gtk.init():
    # GDK reads it exactly once (gdk_pre_parse). apply_color_mgmt_opt_in()
    # guarantees that for CineHDR's own opt-in.
    return gdk_debug_enables_color_mgmt(env.get("GDK_DEBUG", ""))


def with_color_mgmt(gdk_debug: str) -> str:
    """Return a GDK_DEBUG value that enables color-mgmt, keeping other flags."""
    if gdk_debug_enables_color_mgmt(gdk_debug):
        return gdk_debug
    tokens = _gdk_debug_tokens(gdk_debug)
    if any(t.lower() == "all" for t in tokens):
        # "all" already includes color-mgmt; listing it would subtract it.
        tokens = [t for t in tokens if t.lower() != _GDK_COLOR_MGMT]
    else:
        tokens.append(_GDK_COLOR_MGMT)
    return ":".join(tokens)


def color_mgmt_opt_in_requested(env: Mapping[str, str], argv: Sequence[str]) -> bool:
    return env.get(COLOR_MGMT_OPT_IN_ENV) == "1" or COLOR_MGMT_OPT_IN_FLAG in argv


def apply_color_mgmt_opt_in(
    env: MutableMapping[str, str], argv: Sequence[str], gtk_initialized: bool
) -> str:
    """Translate CineHDR's opt-in into GDK_DEBUG=color-mgmt.

    Must run before Gtk is initialized. PyGObject calls Gtk.init_check() when
    gi.repository.Gtk is imported, and GDK reads GDK_DEBUG only then. If GTK
    is already initialized the environment is left untouched, so that
    gtk_cm_opted_in() keeps describing what GTK actually does instead of
    letting the HDR gate believe in color management GTK never enabled.

    Must not import gi: launchers call it before any GTK import.
    """
    if not color_mgmt_opt_in_requested(env, argv):
        return OPT_IN_NOT_REQUESTED
    current = env.get("GDK_DEBUG", "")
    if gdk_debug_enables_color_mgmt(current):
        return OPT_IN_ALREADY_ENABLED
    if gtk_initialized:
        return OPT_IN_TOO_LATE
    env["GDK_DEBUG"] = with_color_mgmt(current)
    return OPT_IN_APPLIED

def gtk_color_managed(caps: Optional[CmCaps], env=os.environ) -> Tuple[bool, str]:
    """(bool, причина): будет ли GTK вообще управлять цветом."""
    if not gtk_cm_opted_in(env):
        return False, "GTK: цветоуправление выключено без GDK_DEBUG=color-mgmt"
    if caps is None:
        return False, "композитор не объявил wp_color_manager_v1"
    if INTENT_PERCEPTUAL not in caps.intents:
        return False, "нет perceptual intent"
    if (FEAT_PARAMETRIC not in caps.features or TF_SRGB not in caps.tfs
            or not (PRIM_SRGB in caps.primaries or FEAT_SET_PRIMARIES in caps.features)):
        return False, "GTK не может создать sRGB-описание (KWin не объявляет TF srgb)"
    return True, "ok"

def gtk_can_tag_hdr(caps: Optional[CmCaps], tf: int = TF_PQ) -> bool:
    return caps is not None and (PRIM_BT2020 in caps.primaries or FEAT_SET_PRIMARIES in caps.features) and tf in caps.tfs

