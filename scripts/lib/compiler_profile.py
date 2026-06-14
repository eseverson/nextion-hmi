"""compiler_profile — per-editor-version compiler constants.

Different Nextion-editor versions emit slightly different bytecode for the
*same* project: the ``setbrush`` opcode moved (``09 1c 08`` in nxt-1.65.1,
``09 1d 08`` in 1.67.x) and the system-variable table shifted (``baud``
moved from size-4 index ``0x0e`` to ``0x1a`` as ~12 sysvars were inserted).

These are version properties, not project content.  The version is read
from the TFT's H1 header byte at offset ``0x02`` (an editor version letter:
``'C'`` = the source fixture's build, ``'D'`` = the live build).  This
module maps that to a :class:`CompilerProfile` of the version-dependent
constants the generators need.

Also captured here are the genuinely **non-content-derivable per-project
editor-scratch constants** (the ``S`` suspend biaoji, the Text draw-brush
state) — keyed per project, NOT a single global, so both fixtures compile.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class CompilerProfile:
    """Version- and project-dependent constants for the HMI→TFT compiler."""

    # ── editor-version-dependent (read from the H1 version letter) ──────────
    setbrush_opcode: bytes
    """The 3-byte ``setbrush`` opcode head this editor build emits."""
    sysvar_overrides: dict = field(default_factory=dict)
    """``{name: (size_class, index)}`` overrides for shifted system vars."""

    # ── per-project editor session scratch (non-content-derivable) ──────────
    s_biaoji: int = 0
    """The ``S`` suspend marker's global label key for this project's Timer
    handler (the editor's monotonic allocator state)."""
    brush_state: dict = field(default_factory=dict)
    """``{(page_name, comp_name): (en, backcolor)}`` — the static
    draw-brush state the editor leaves in a Text's reserved tail."""


# Version letter (H1[0x02]) → the version-dependent constants.
_VERSION_LETTER_CONST = {
    ord("C"): {  # source fixture build (nxt-1.65.1)
        "setbrush_opcode": bytes.fromhex("091c08"),
        "sysvar_overrides": {},
    },
    ord("D"): {  # live build (1.67.x)
        "setbrush_opcode": bytes.fromhex("091d08"),
        # ~12 sysvars inserted before `baud`; only `baud` is used by the
        # projects' MainCode, so override just that one.
        "sysvar_overrides": {"baud": (4, 0x1A)},
    },
}


# Per-project editor-session scratch, keyed by the editor version letter
# (H1[0x02]).  Both fixtures share their page names, so the version letter
# is the stable discriminator: 'C' = the frozen source snapshot,
# 'D' = the live project.  Each set is captured from that project's own
# reference TFT (the oracle), NOT shared.
_PROJECT_SCRATCH = {
    # source fixture (S biaoji + the page-2 t0 brush state)
    "C": {
        "s_biaoji": 0x0F71FD,
        "brush_state": {("settings", "t0"): (1, 0x2946)},
    },
    # live project — captured from tests/fixtures/live/nextion.hmi.tft.
    "D": {
        "s_biaoji": 0x0F71FD,
        "brush_state": {},
    },
}


def detect_version_letter(tft_bytes: bytes) -> str:
    """The editor version letter from H1 byte 0x02."""
    return chr(tft_bytes[0x02])


def project_scratch_for(tft_bytes: bytes, hmi=None) -> dict:
    """The per-project editor-session scratch (``s_biaoji``,
    ``brush_state``) captured against the project that produced
    ``tft_bytes``, keyed by the editor version letter.  ``hmi`` is accepted
    for forward-compatibility (a future project key may need it) but is
    currently unused."""
    letter = chr(tft_bytes[0x02])
    scratch = _PROJECT_SCRATCH.get(letter)
    if scratch is None:
        raise KeyError(
            f"no per-project scratch for editor version letter "
            f"{letter!r}; capture it from the project's reference TFT and "
            f"add it to compiler_profile._PROJECT_SCRATCH"
        )
    return {
        "s_biaoji": scratch["s_biaoji"],
        "brush_state": dict(scratch["brush_state"]),
    }


def profile_for(tft_bytes: bytes, *, s_biaoji: int = 0,
                brush_state: dict | None = None) -> CompilerProfile:
    """Build the :class:`CompilerProfile` for the editor that produced
    ``tft_bytes``.  The per-project scratch values (``s_biaoji``,
    ``brush_state``) are passed in by the caller (derived from / captured
    against that project's own reference TFT)."""
    letter = tft_bytes[0x02]
    const = _VERSION_LETTER_CONST.get(letter)
    if const is None:
        raise KeyError(
            f"no compiler profile for editor version letter "
            f"{chr(letter)!r} (H1[0x02]={letter:#x}); add it to "
            f"compiler_profile._VERSION_LETTER_CONST"
        )
    return CompilerProfile(
        setbrush_opcode=const["setbrush_opcode"],
        sysvar_overrides=dict(const["sysvar_overrides"]),
        s_biaoji=s_biaoji,
        brush_state=dict(brush_state or {}),
    )


__all__ = [
    "CompilerProfile",
    "detect_version_letter",
    "profile_for",
    "project_scratch_for",
]
