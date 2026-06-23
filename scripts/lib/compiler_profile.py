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

Also captured here is the genuinely **non-content-derivable per-project
editor-scratch** — the Text draw-brush state — keyed per project so both
fixtures compile.

(The ``S`` suspend biaoji used to be captured here too; it is now DERIVED in
``usercode_assembler.suspend_biaoji`` — the editor's fixed first-suspend-marker
seed, identical for every project — so it no longer needs per-project
capture; see me/miata-dash#40.)
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class CompilerProfile:
    """Version- and project-dependent constants for the HMI→TFT compiler."""

    # ── editor-version-dependent (read from the H1 version letter) ──────────
    setbrush_opcode: bytes
    """The 3-byte ``setbrush`` opcode head this editor build emits."""
    doevents_opcode: bytes = bytes.fromhex("092208")
    """The 3-byte page-load ``doevents`` opcode (``09 22 08`` C / ``09 23 08``
    D — the ``09 xx 08`` opcode family's index is +1 in the newer build)."""
    timerset_opcode: bytes = bytes.fromhex("092308")
    """The 3-byte page-load ``timerset`` opcode (``09 23 08`` C / ``09 24 08``
    D)."""
    nstr_opcode: bytes = bytes.fromhex("091304")
    """The 3-byte Number ``nstr`` draw opcode (``09 13 04`` C / ``09 15 04``
    D); me/miata-dash#38 byte-verified against the 'D'-build Number block."""
    sysvar_overrides: dict = field(default_factory=dict)
    """``{name: (size_class, index)}`` overrides for shifted system vars."""
    frame_prefix: int = 0x10
    """The media-frame runtime-header size (``objdatarampos`` base): 0x10 in
    the source/C build, 0x40 in the 'D' build (which also pulls global-scope
    objects into a prefix slot)."""

    # ── per-project editor session scratch (non-content-derivable) ──────────
    brush_state: dict = field(default_factory=dict)
    """``{(page_name, comp_name): (en, backcolor)}`` — the static
    draw-brush state the editor leaves in a Text's reserved tail."""
    gtext_scroll: dict = field(default_factory=dict)
    """``{comp_name: (step, motion_addr)}`` — a GText's scroll-arming scratch
    (the ``09 24 08`` step literal + the ``4c 20 03`` motion-jump label
    biaoji).  Both differ between saves of an identical component, so they are
    captured per-project from the reference TFT (me/miata-dash#38).  ``"*"``
    is the wildcard key (every GText shares the value)."""


# Version letter (H1[0x02]) → the version-dependent constants.
_VERSION_LETTER_CONST = {
    ord("C"): {  # source fixture build (nxt-1.65.1)
        "setbrush_opcode": bytes.fromhex("091c08"),
        "doevents_opcode": bytes.fromhex("092208"),
        "timerset_opcode": bytes.fromhex("092308"),
        "nstr_opcode": bytes.fromhex("091304"),
        "sysvar_overrides": {},
        "frame_prefix": 0x10,
    },
    ord("D"): {  # live build (1.67.x)
        # The `09 xx 08` opcode family's index is +1 in this build.
        "setbrush_opcode": bytes.fromhex("091d08"),
        "doevents_opcode": bytes.fromhex("092308"),
        "timerset_opcode": bytes.fromhex("092408"),
        "nstr_opcode": bytes.fromhex("091504"),
        # Newer build inserted sysvars, shifting size-4 indices: `baud`
        # 0x0e→0x1a, `dims` 0x14→0x21.  Override the ones the projects'
        # bytecode actually references.
        "sysvar_overrides": {"baud": (4, 0x1A), "dims": (4, 0x21)},
        "frame_prefix": 0x40,
    },
}


# Per-project editor-session scratch, keyed by the editor version letter
# (H1[0x02]).  Both fixtures share their page names, so the version letter
# is the stable discriminator: 'C' = the frozen source snapshot,
# 'D' = the live project.  Each set is captured from that project's own
# reference TFT (the oracle), NOT shared.
_PROJECT_SCRATCH = {
    # source fixture (the page-2 t0 draw-brush state)
    "C": {
        "brush_state": {("settings", "t0"): (1, 0x2946)},
    },
    # live project — captured from tests/fixtures/live/nextion.hmi.tft
    # (same settings/t0 draw-brush carry as the source).
    "D": {
        "brush_state": {("settings", "t0"): (1, 0x2946)},
    },
}


def detect_version_letter(tft_bytes: bytes) -> str:
    """The editor version letter from H1 byte 0x02."""
    return chr(tft_bytes[0x02])


def project_scratch_for(tft_bytes: bytes, hmi=None) -> dict:
    """The per-project editor-session scratch (``brush_state``) captured
    against the project that produced ``tft_bytes``, keyed by the editor
    version letter.  ``hmi`` is accepted for forward-compatibility (a future
    project key may need it) but is currently unused."""
    letter = chr(tft_bytes[0x02])
    scratch = _PROJECT_SCRATCH.get(letter)
    if scratch is None:
        raise KeyError(
            f"no per-project scratch for editor version letter "
            f"{letter!r}; capture it from the project's reference TFT and "
            f"add it to compiler_profile._PROJECT_SCRATCH"
        )
    out = {
        "brush_state": dict(scratch["brush_state"]),
    }
    # GText scroll scratch (step literal + motion-jump label) is recovered
    # from the reference TFT itself when present, since it differs between
    # otherwise-identical projects (me/miata-dash#38).
    gs = _recover_gtext_scroll(tft_bytes, hmi)
    if gs:
        out["gtext_scroll"] = gs
    return out


def _recover_gtext_scroll(tft_bytes: bytes, hmi=None) -> dict:
    """Recover each GText's ``(step, motion_addr)`` scroll scratch from the
    reference TFT by decoding its slot-0 ``09 24 08`` scroll-init block and
    the following ``4c 20 03`` motion marker.  Returns ``{comp_name:
    (step, motion_addr)}`` (empty if no GText / no scroll block)."""
    import struct as _struct
    if hmi is None:
        return {}
    try:
        from scripts.lib.tft_attrs import parse_appinf1_corrected
        from scripts.lib.h2_cipher import encrypt as _h2
    except Exception:
        return {}
    mc = int.from_bytes(tft_bytes[0x2E:0x32], "little")
    a1 = parse_appinf1_corrected(_h2(tft_bytes[0xC8:0xC8 + 196], mc))
    strd = a1["strdataaddr"]; objbase = a1["objxinxiadd"]; pageadd = a1["pageadd"]
    out: dict = {}
    for pi, hp in enumerate(hmi):
        pe = tft_bytes[pageadd + pi * 16:pageadd + pi * 16 + 16]
        objstar = _struct.unpack_from("<H", pe, 0)[0]
        for ci, hc in enumerate(hp.components):
            if hc.lei != 55:
                continue
            e = tft_bytes[objbase + (objstar + ci) * 232:
                          objbase + (objstar + ci) * 232 + 232]
            init = _struct.unpack_from("<I", e, 4)[0]
            blob = tft_bytes[strd + init:strd + init + 256]
            si = blob.find(b"\x09\x24\x08")
            if si < 0:
                continue
            # 09 24 08 LOAD(en)[5] , <step ascii> , LOAD(tim)[5] , 0
            p = si + 3 + 5 + 1            # past opcode + LOAD(en) + ','
            j = blob.index(b",", p)
            step = int(blob[p:j])
            mi = blob.find(b"\x4c\x20\x03", si)
            motion = _struct.unpack_from("<I", blob, mi + 3)[0] if mi >= 0 else 0
            out[hc.name] = (step, motion)
    return out


def profile_for(tft_bytes: bytes, *,
                brush_state: dict | None = None,
                gtext_scroll: dict | None = None) -> CompilerProfile:
    """Build the :class:`CompilerProfile` for the editor that produced
    ``tft_bytes``.  The per-project scratch (``brush_state``, ``gtext_scroll``)
    is passed in by the caller (captured against that project's own reference
    TFT)."""
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
        doevents_opcode=const.get("doevents_opcode", bytes.fromhex("092208")),
        timerset_opcode=const.get("timerset_opcode", bytes.fromhex("092308")),
        nstr_opcode=const.get("nstr_opcode", bytes.fromhex("091304")),
        sysvar_overrides=dict(const["sysvar_overrides"]),
        frame_prefix=const.get("frame_prefix", 0x10),
        brush_state=dict(brush_state or {}),
        gtext_scroll=dict(gtext_scroll or {}),
    )


__all__ = [
    "CompilerProfile",
    "detect_version_letter",
    "profile_for",
    "project_scratch_for",
]
