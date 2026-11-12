"""MilkDrop presets for the Now Playing visualizer: the rules an added
preset follows, and the ``.milk`` reader.

The visualizer is Butterchurn 3 (``web/static/vendor/butterchurn/``), a
WebGL MilkDrop 2 engine. It runs a preset given as JSON: ``baseVals``,
up to four custom ``shapes`` and four custom ``waves``, the equations as
MilkDrop's own EEL text (``*_eqs_eel``, compiled by the engine to
WebAssembly, so they can only touch the preset's own variables), and the
warp / composite shaders as GLSL (``warp``, ``comp``; empty means the
engine's standard ones).

Two kinds of file are accepted from an admin:

* **Butterchurn JSON** in that format (what ``butterchurn-presets`` 3.x
  ships). The older format carries its equations as JavaScript
  (``*_eqs_str``) that the engine would run through ``new Function`` in
  every browser of the house: that is refused, never run, and any
  ``*_str`` field beside EEL ones is dropped.
* **A classic ``.milk`` file.** Its settings and equations are copied over
  as they are (MilkDrop's EEL is what the engine runs). Its custom shaders
  are HLSL, which needs a shader compiler to become GLSL; Domovoi doesn't
  carry one, so they are left out and the preset draws with the standard
  warp and composite shaders. The upload's ``notes`` say so, and the
  dashboard shows them.

Stored one file per preset under ``visualizer_presets_dir``::

    {"id", "name", "source": "json" | "milk", "added_at", "notes": [...],
     "preset": {...}}

The key-name table for ``.milk`` settings follows the MilkDrop 2 preset
format as documented by MilkDrop's preset authoring guide and as read by
Butterchurn's own tools (milkdrop-preset-utils, MIT).

Pure functions plus small file helpers; no database, no network.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import tempfile
import unicodedata
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

#: The upload as text, and the stored document.
MAX_UPLOAD_BYTES = 256 * 1024
#: Added presets kept for the house.
MAX_PRESETS = 300
NAME_MAX = 80
#: One equation block or shader.
MAX_CODE_CHARS = 64 * 1024
MAX_BASEVALS = 200
SLOTS = 4  # custom shapes, custom waves

ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,62}$")
VAL_KEY_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,39}$")

PRESET_EQ_KEYS = ("init_eqs_eel", "frame_eqs_eel", "pixel_eqs_eel")
SHAPE_EQ_KEYS = ("init_eqs_eel", "frame_eqs_eel")
WAVE_EQ_KEYS = ("init_eqs_eel", "frame_eqs_eel", "point_eqs_eel")

LIMITS = {
    "max_bytes": MAX_UPLOAD_BYTES,
    "max_presets": MAX_PRESETS,
    "name_max": NAME_MAX,
    "accepts": [".json", ".milk"],
}

SHADERS_DROPPED = (
    "this preset's custom shaders (MilkDrop 2 warp/composite) can't be converted here, "
    "so it draws with the standard ones and may look different from MilkDrop"
)


class PresetError(ValueError):
    """A preset this server won't keep; the message says why, in words."""


# ─── Names and ids ───────────────────────────────────────────────────────


def clean_name(raw: Any) -> str:
    """A display name: the file name without its extension, control and
    formatting characters dropped, spaces collapsed, at most NAME_MAX."""
    if not isinstance(raw, str):
        raise PresetError("the name must be text")
    base = raw.replace("\\", "/").rsplit("/", 1)[-1]
    for ext in (".json", ".milk"):
        if base.lower().endswith(ext):
            base = base[: -len(ext)]
            break
    kept = "".join(" " if unicodedata.category(ch)[0] == "C" else ch for ch in base)
    name = " ".join(kept.split())[:NAME_MAX].strip()
    if not name:
        raise PresetError("the preset needs a name")
    return name


def make_id(name: str, preset: dict[str, Any]) -> str:
    """slug of the name + a hash of the preset: the same preset added twice
    gets the same id (and is refused as already there)."""
    slug = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode().lower()
    slug = re.sub(r"[^a-z0-9]+", "-", slug).strip("-")[:40].strip("-") or "preset"
    digest = hashlib.sha256(json.dumps(preset, sort_keys=True).encode()).hexdigest()[:10]
    return f"{slug}-{digest}"


def valid_id(preset_id: str) -> bool:
    return isinstance(preset_id, str) and bool(ID_RE.match(preset_id))


# ─── Butterchurn JSON ────────────────────────────────────────────────────


def _code(raw: Any, where: str) -> str:
    if raw is None:
        return ""
    if not isinstance(raw, str):
        raise PresetError(f"{where} must be text")
    if len(raw) > MAX_CODE_CHARS:
        raise PresetError(f"{where} is too long")
    if "\x00" in raw:
        raise PresetError(f"{where} has a character that can't be in a preset")
    return raw


def _vals(raw: Any, where: str) -> dict[str, float]:
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise PresetError(f"{where} must be an object of numbers")
    if len(raw) > MAX_BASEVALS:
        raise PresetError(f"{where} has too many values")
    out: dict[str, float] = {}
    for k, v in raw.items():
        if not isinstance(k, str) or not VAL_KEY_RE.match(k):
            raise PresetError(f"{where}: {str(k)[:40]!r} is not a setting name")
        if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v):
            raise PresetError(f"{where}.{k} must be a number")
        out[k] = v
    return out


def _slots(raw: Any, where: str, keys: tuple[str, ...]) -> list[dict[str, Any]]:
    if raw is None:
        raw = []
    if not isinstance(raw, list):
        raise PresetError(f"{where} must be a list")
    if len(raw) > SLOTS:
        raise PresetError(f"{where}: at most {SLOTS}")
    out = []
    for i, item in enumerate(raw):
        if not isinstance(item, dict):
            raise PresetError(f"{where}[{i}] must be an object")
        _refuse_js(item, f"{where}[{i}]", keys)
        slot: dict[str, Any] = {"baseVals": _vals(item.get("baseVals"), f"{where}[{i}].baseVals")}
        for k in keys:
            slot[k] = _code(item.get(k), f"{where}[{i}].{k}")
        out.append(slot)
    while len(out) < SLOTS:
        out.append({"baseVals": {"enabled": 0}, **{k: "" for k in keys}})
    return out


def _refuse_js(obj: dict[str, Any], where: str, eel_keys: tuple[str, ...]) -> None:
    has_js = any(isinstance(k, str) and k.endswith("_eqs_str") and obj.get(k) for k in obj)
    has_eel = any(k in obj for k in eel_keys)
    if has_js and not has_eel:
        raise PresetError(
            "this is the older Butterchurn format, whose equations are JavaScript; "
            "Domovoi only runs MilkDrop's own equations. Add the original .milk file "
            "or a Butterchurn 3 preset instead"
            + ("" if where == "preset" else f" ({where})")
        )


def validate_preset(body: Any) -> dict[str, Any]:
    """The preset as stored (every field the engine reads, nothing else),
    or :class:`PresetError`. Pure."""
    if not isinstance(body, dict):
        raise PresetError("a preset must be a JSON object")
    _refuse_js(body, "preset", PRESET_EQ_KEYS)
    if "baseVals" not in body and not any(k in body for k in PRESET_EQ_KEYS):
        raise PresetError("this JSON isn't a Butterchurn preset (no baseVals or equations)")
    version = body.get("version", 2)
    if version not in (1, 2):
        raise PresetError("unknown preset version (1 or 2)")
    out: dict[str, Any] = {
        "version": version,
        "baseVals": _vals(body.get("baseVals"), "baseVals"),
        "shapes": _slots(body.get("shapes"), "shapes", SHAPE_EQ_KEYS),
        "waves": _slots(body.get("waves"), "waves", WAVE_EQ_KEYS),
    }
    for k in PRESET_EQ_KEYS:
        out[k] = _code(body.get(k), k)
    out["warp"] = _code(body.get("warp"), "warp")
    out["comp"] = _code(body.get("comp"), "comp")
    return out


# ─── .milk ───────────────────────────────────────────────────────────────

#: .milk setting (lower-cased) -> Butterchurn baseVals key, for the preset
#: and for its shapes and waves (one namespace, as MilkDrop names them).
MILK_KEYS: dict[str, str] = {
    "frating": "rating", "fgammaadj": "gammaadj", "fdecay": "decay",
    "fvideoechozoom": "echo_zoom", "fvideoechoalpha": "echo_alpha",
    "nvideoechoorientation": "echo_orient", "nwavemode": "wave_mode",
    "badditivewaves": "additivewave", "bwavedots": "wave_dots", "bwavethick": "wave_thick",
    "bmodwavealphabyvolume": "modwavealphabyvolume", "bmaximizewavecolor": "wave_brighten",
    "btexwrap": "wrap", "bdarkencenter": "darken_center", "bredbluestereo": "red_blue",
    "bbrighten": "brighten", "bdarken": "darken", "bsolarize": "solarize", "binvert": "invert",
    "fwavealpha": "wave_a", "fwavescale": "wave_scale", "fwavesmoothing": "wave_smoothing",
    "fwaveparam": "wave_mystery", "fmodwavealphastart": "modwavealphastart",
    "fmodwavealphaend": "modwavealphaend", "fwarpanimspeed": "warpanimspeed",
    "fwarpscale": "warpscale", "fzoomexponent": "zoomexp", "fshader": "fshader",
    "nmotionvectorsx": "mv_x", "nmotionvectorsy": "mv_y",
    # shapes and waves
    "thick": "thickoutline", "instances": "num_inst", "num_instances": "num_inst",
    "badditive": "additive", "busedots": "usedots", "bspectrum": "spectrum",
    "bdrawthick": "thick",
}

def _milk_value(raw: str) -> float | None:
    try:
        v = float(raw.strip())
    except ValueError:
        return None
    return v if math.isfinite(v) else None


def _join(lines: dict[int, str]) -> str:
    return "\n".join(lines[n] for n in sorted(lines))


def parse_milk(text: str) -> tuple[dict[str, Any], list[str]]:
    """A ``.milk`` file as a Butterchurn 3 preset, and notes for the person
    who added it. Pure."""
    if not isinstance(text, str) or "=" not in text:
        raise PresetError("this doesn't look like a .milk preset")
    vals: dict[str, float] = {}
    code: dict[str, dict[int, str]] = {}
    shape_vals: list[dict[str, float]] = [{} for _ in range(SLOTS)]
    wave_vals: list[dict[str, float]] = [{} for _ in range(SLOTS)]
    milk_version = 0.0
    seen_section = False
    for raw_line in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        line = raw_line.strip()
        if not line or line.startswith(";") or line.startswith("//"):
            continue
        if line.startswith("[") and line.endswith("]"):
            seen_section = True
            continue
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        low = key.lower()
        if low == "milkdrop_preset_version":
            milk_version = _milk_value(value) or 0.0
            continue
        m = re.match(r"^(shapecode|wavecode)_([0-3])_(\w+)$", low)
        if m:
            v = _milk_value(value)
            if v is not None:
                name = MILK_KEYS.get(m.group(3), m.group(3))
                (shape_vals if m.group(1) == "shapecode" else wave_vals)[int(m.group(2))][name] = v
            continue
        m = re.match(
            r"^(per_frame_init|per_frame|per_pixel|warp|comp"
            r"|shape_[0-3]_init|shape_[0-3]_per_frame"
            r"|wave_[0-3]_init|wave_[0-3]_per_frame|wave_[0-3]_per_point)_?(\d+)$",
            low,
        )
        if m:
            block = m.group(1)
            if block in ("warp", "comp") and value.startswith("`"):
                value = value[1:]
            code.setdefault(block, {})[int(m.group(2))] = value
            continue
        v = _milk_value(value)
        if v is not None and VAL_KEY_RE.match(key):
            vals[MILK_KEYS.get(low, low)] = v
    if not vals and not code:
        raise PresetError("this doesn't look like a .milk preset")
    if not seen_section and not vals:
        raise PresetError("this doesn't look like a .milk preset")

    def block(name: str) -> str:
        return _join(code.get(name, {}))

    notes: list[str] = []
    if block("warp").strip() or block("comp").strip():
        notes.append(SHADERS_DROPPED)
    preset = {
        "version": 2 if milk_version >= 200 else 1,
        "baseVals": vals,
        "shapes": [
            {"baseVals": shape_vals[i],
             "init_eqs_eel": block(f"shape_{i}_init"),
             "frame_eqs_eel": block(f"shape_{i}_per_frame")}
            for i in range(SLOTS)
        ],
        "waves": [
            {"baseVals": wave_vals[i],
             "init_eqs_eel": block(f"wave_{i}_init"),
             "frame_eqs_eel": block(f"wave_{i}_per_frame"),
             "point_eqs_eel": block(f"wave_{i}_per_point")}
            for i in range(SLOTS)
        ],
        "init_eqs_eel": block("per_frame_init"),
        "frame_eqs_eel": block("per_frame"),
        "pixel_eqs_eel": block("per_pixel"),
        "warp": "",
        "comp": "",
    }
    return validate_preset(preset), notes


def convert_upload(filename: Any, content: Any) -> tuple[str, str, dict[str, Any], list[str]]:
    """(name, source, preset, notes) for an uploaded file, or PresetError."""
    name = clean_name(filename)
    if not isinstance(content, str):
        raise PresetError("the file must be text")
    if len(content.encode("utf-8")) > MAX_UPLOAD_BYTES:
        raise PresetError(f"the file is too large ({MAX_UPLOAD_BYTES // 1024} KB at most)")
    lower = str(filename).lower()
    stripped = content.lstrip("﻿").lstrip()
    if lower.endswith(".milk"):
        preset, notes = parse_milk(stripped)
        return name, "milk", preset, notes
    if not lower.endswith(".json"):
        raise PresetError("add a .milk file or a Butterchurn .json preset")
    try:
        body = json.loads(stripped)
    except ValueError as e:
        raise PresetError("the file isn't valid JSON") from e
    return name, "json", validate_preset(body), []


# ─── The store ───────────────────────────────────────────────────────────


def presets_dir() -> Path:
    from domovoi.config import settings

    raw = getattr(settings, "visualizer_presets_dir", "") or "~/.domovoi/visualizer_presets"
    return Path(os.path.expanduser(raw))


def _path(root: Path, preset_id: str) -> Path:
    if not valid_id(preset_id):
        raise PresetError("not a preset id")
    path = (root / f"{preset_id}.json").resolve()
    if path.parent != root.resolve():
        raise PresetError("not a preset id")
    return path


def _read(path: Path) -> dict[str, Any] | None:
    try:
        if path.stat().st_size > MAX_UPLOAD_BYTES * 2:
            return None
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(doc, dict) or not valid_id(str(doc.get("id", ""))):
        return None
    if doc["id"] != path.stem or not isinstance(doc.get("preset"), dict):
        return None
    return doc


def summary(doc: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": doc["id"],
        "name": str(doc.get("name") or doc["id"])[:NAME_MAX],
        "source": doc.get("source") if doc.get("source") in ("json", "milk") else "json",
        "added_at": doc.get("added_at"),
        "notes": [str(n) for n in (doc.get("notes") or []) if isinstance(n, str)][:4],
    }


def list_added(root: Path | None = None) -> list[dict[str, Any]]:
    root = root or presets_dir()
    if not root.is_dir():
        return []
    out = []
    for p in root.glob("*.json"):
        if p.is_symlink() or not p.is_file():
            continue
        doc = _read(p)
        if doc:
            out.append(summary(doc))
    out.sort(key=lambda d: d["name"].lower())
    return out


def read_added(preset_id: str, root: Path | None = None) -> dict[str, Any] | None:
    root = root or presets_dir()
    path = _path(root, preset_id)
    if path.is_symlink() or not path.is_file():
        return None
    return _read(path)


def save_added(name: str, source: str, preset: dict[str, Any], notes: list[str],
               root: Path | None = None) -> dict[str, Any]:
    """Store a validated preset. PresetError when it is already there or the
    house is at the cap (FileExistsError is not leaked)."""
    root = root or presets_dir()
    root.mkdir(parents=True, exist_ok=True)
    preset_id = make_id(name, preset)
    path = _path(root, preset_id)
    if path.exists():
        raise PresetError("that preset is already added")
    if len(list(root.glob("*.json"))) >= MAX_PRESETS:
        raise PresetError(f"the house already has {MAX_PRESETS} added presets; remove some first")
    doc = {
        "id": preset_id,
        "name": name,
        "source": source,
        "added_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "notes": notes,
        "preset": preset,
    }
    fd, tmp = tempfile.mkstemp(prefix=".upload-", suffix=".tmp", dir=str(root))
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(json.dumps(doc, ensure_ascii=False).encode("utf-8"))
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    return summary(doc)


def delete_added(preset_id: str, root: Path | None = None) -> bool:
    root = root or presets_dir()
    path = _path(root, preset_id)
    if path.is_symlink() or not path.is_file():
        return False
    path.unlink()
    return True
