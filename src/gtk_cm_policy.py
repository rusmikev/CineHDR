from typing import Optional, Tuple
from dataclasses import dataclass
import os

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

def gtk_cm_opted_in(env=os.environ) -> bool:
    flags = {f.strip().lower() for f in env.get("GDK_DEBUG", "").split(":")}
    return "color-mgmt" in flags or "all" in flags

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

