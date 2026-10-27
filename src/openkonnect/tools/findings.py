"""Locators from kicad-tools findings, read through kicad-tools' own output models.

kicad-tools has no single, stable finding format yet (gap G1,
rjwalters/kicad-tools#8015). Each finding-emitting command serialises its own
model with that model's ``to_dict()``. This module does not copy those
shapes. For every supported model it imports the class from the installed
kicad-tools and:

* recognises a JSON finding as that model when the dict carries every key the
  model's own ``to_dict()`` always emits (computed at run time from a probe
  instance, so the key set follows the installed version);
* reads only the model's locator fields (names checked against the model's
  dataclass fields at run time) to get references, ``REF.PAD`` names, net
  names, points and a layer.

A finding that matches no model, or matches more than one equally well, is
not guessed at: the caller turns it into ``invalid_argument``. If a model
field this module relies on disappears upstream, that model is disabled and
its findings stop being recognised, which the kicad-tools-main CI job sees.

Frames (spec C-1). A model's point is used only when its frame is known:

* ``kct check`` (``validate.violations.DRCViolation.location``): ``file``.
  The model does not state a frame; kct 0.24 was observed (black-box, on
  ``tests/fixtures/selection.kicad_pcb``) to report footprint positions
  exactly as stored in the file, and the round-trip test pins that.
* ``kct net-status`` (``PadInfo.position``): ``board``, as the model's own
  field comment says ("Board coordinates"), also observed on the fixture.
* ``kct detect-mistakes`` (``Mistake.location``) and ``kct drc``
  (``drc.violation.Location``): the models do not state a frame, so their
  points are reported as ``frame_not_stated`` and never used.
"""

from __future__ import annotations

import dataclasses
import importlib
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

#: Where the stable finding format is being asked for upstream (gap G1).
G1_UPSTREAM = "https://github.com/rjwalters/kicad-tools/issues/8015"


@dataclass(frozen=True)
class Locator:
    """One way a finding points at the board.

    ``kind``: ``ref`` (reference designator), ``pad`` (``REF.PAD``), ``net``,
    ``name`` (a name the model does not type: ref, pad or net), or ``point``.
    """

    kind: str
    value: Any
    field: str
    frame: str | None = None
    layer: str | None = None

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {"kind": self.kind, "field": self.field}
        if self.kind == "point":
            d["x_mm"], d["y_mm"] = self.value
            d["frame"] = self.frame
        else:
            d["value"] = self.value
        if self.layer:
            d["layer"] = self.layer
        return d


Getter = Callable[[str], Any]


@dataclass
class Adapter:
    """How to read one kicad-tools finding model's locators."""

    source: str
    module: str
    cls_name: str
    fields: tuple[str, ...]
    probe: Callable[[type, Any], Any]
    extract: Callable[[Getter, Any], list[Locator]]
    model: type | None = None
    signature: frozenset[str] = frozenset()
    disabled: str | None = None
    module_obj: Any = field(default=None, repr=False)

    @property
    def qualname(self) -> str:
        return f"{self.module}.{self.cls_name}"

    def load(self) -> None:
        try:
            mod = importlib.import_module(self.module)
            model = getattr(mod, self.cls_name)
            names = {f.name for f in dataclasses.fields(model)}
            missing = [f for f in self.fields if f not in names]
            if missing:
                raise LookupError(f"fields no longer on the model: {missing}")
            sample = self.probe(model, mod)
            self.signature = frozenset(sample.to_dict().keys())
            self.model = model
            self.module_obj = mod
        except Exception as e:  # any upstream change disables just this model
            self.disabled = f"{type(e).__name__}: {e}"


def _seq(v: Any) -> list[Any]:
    if v is None:
        return []
    if isinstance(v, (list, tuple)):
        return list(v)
    return [v]


def _xy(v: Any) -> tuple[float, float] | None:
    """A point given as ``(x, y)`` / ``[x, y]``."""
    if isinstance(v, (list, tuple)) and len(v) == 2 and all(isinstance(c, (int, float)) for c in v):
        return float(v[0]), float(v[1])
    return None


def _strs(v: Any) -> list[str]:
    return [s for s in _seq(v) if isinstance(s, str) and s]


# -- kct check: kicad_tools.validate.violations.DRCViolation -------------------


def _check_probe(model: type, _mod: Any) -> Any:
    return model(rule_id="probe", severity="error", message="")


def _check_extract(get: Getter, _mod: Any) -> list[Locator]:
    layer = get("layer") if isinstance(get("layer"), str) else None
    out = [Locator("name", s, "items") for s in _strs(get("items"))]
    out += [Locator("net", s, "nets") for s in _strs(get("nets"))]
    p = _xy(get("location"))
    if p is not None:
        out.append(Locator("point", p, "location", "file", layer))
    for c in _seq(get("closest_locations")):
        p = _xy(c)
        if p is not None:
            out.append(Locator("point", p, "closest_locations", "file", layer))
    return out


# -- kct drc: kicad_tools.drc.violation.DRCViolation ---------------------------


def _drc_probe(model: type, mod: Any) -> Any:
    return model(
        type=list(mod.ViolationType)[0],
        type_str="",
        severity=list(mod.Severity)[0],
        message="",
    )


def _drc_extract(get: Getter, mod: Any) -> list[Locator]:
    descriptions = _strs(get("items"))
    # kicad-cli describes items in free text ("Pad 6 [GND] of U3 on F.Cu");
    # kicad-tools' own normaliser pulls the reference designators out.
    refs = sorted(mod.extract_item_refs(descriptions)) if descriptions else []
    out = [Locator("ref", r, "items") for r in refs]
    out += [Locator("net", s, "nets") for s in _strs(get("nets"))]
    for loc in _seq(get("locations")):
        if isinstance(loc, dict):
            x, y, layer = loc.get("x_mm"), loc.get("y_mm"), loc.get("layer")
        else:
            x, y, layer = getattr(loc, "x_mm", None), getattr(loc, "y_mm", None), loc.layer
        p = _xy([x, y])
        if p is not None:
            out.append(Locator("point", p, "locations", None, layer or None))
    return out


# -- kct detect-mistakes: kicad_tools.explain.mistakes.Mistake -----------------


def _mistake_probe(model: type, mod: Any) -> Any:
    return model(
        category=list(mod.MistakeCategory)[0],
        severity="warning",
        title="",
        components=[],
        explanation="",
        fix_suggestion="",
    )


def _mistake_extract(get: Getter, _mod: Any) -> list[Locator]:
    out = [Locator("name", s, "components") for s in _strs(get("components"))]
    p = _xy(get("location"))
    if p is not None:
        out.append(Locator("point", p, "location", None))
    return out


# -- kct net-status: kicad_tools.analysis.net_status.NetStatus -----------------


def _net_status_probe(model: type, _mod: Any) -> Any:
    return model(net_number=0, net_name="probe")


def _net_status_extract(get: Getter, _mod: Any) -> list[Locator]:
    out = [Locator("net", s, "net_name") for s in _strs(get("net_name"))]
    for fname in ("connected_pads", "unconnected_pads"):
        for pad in _seq(get(fname)):
            if isinstance(pad, dict):
                name, pos = pad.get("name"), pad.get("position")
            else:  # PadInfo
                name, pos = getattr(pad, "full_name", None), getattr(pad, "position", None)
            if isinstance(name, str) and name:
                out.append(Locator("pad", name, fname))
            p = _xy(pos)
            if p is not None:
                out.append(Locator("point", p, fname, "board"))
    return out


def _adapters() -> list[Adapter]:
    return [
        Adapter(
            "kct check",
            "kicad_tools.validate.violations",
            "DRCViolation",
            ("items", "nets", "location", "closest_locations", "layer"),
            _check_probe,
            _check_extract,
        ),
        Adapter(
            "kct drc",
            "kicad_tools.drc.violation",
            "DRCViolation",
            ("items", "nets", "locations"),
            _drc_probe,
            _drc_extract,
        ),
        Adapter(
            "kct detect-mistakes",
            "kicad_tools.explain.mistakes",
            "Mistake",
            ("components", "location"),
            _mistake_probe,
            _mistake_extract,
        ),
        Adapter(
            "kct net-status",
            "kicad_tools.analysis.net_status",
            "NetStatus",
            ("net_name", "connected_pads", "unconnected_pads"),
            _net_status_probe,
            _net_status_extract,
        ),
    ]


_LOADED: list[Adapter] | None = None


def adapters() -> list[Adapter]:
    global _LOADED
    if _LOADED is None:
        loaded = _adapters()
        for a in loaded:
            a.load()
        _LOADED = loaded
    return _LOADED


def models_status() -> list[dict[str, Any]]:
    return [
        {"source": a.source, "model": a.qualname, "enabled": a.disabled is None}
        | ({"disabled": a.disabled} if a.disabled else {})
        for a in adapters()
    ]


class Unrecognised(Exception):
    def __init__(self, reason: str, candidates: list[str] | None = None) -> None:
        super().__init__(reason)
        self.reason = reason
        self.candidates = candidates or []


def match(finding: Any) -> Adapter:
    """The model a finding (dict or model object) belongs to, or :class:`Unrecognised`."""
    live = [a for a in adapters() if a.disabled is None]
    if not isinstance(finding, dict):
        for a in live:
            if a.model is not None and isinstance(finding, a.model):
                return a
        raise Unrecognised(f"not a JSON object or a known kicad-tools model ({type(finding)})")
    keys = set(finding)
    hits = [a for a in live if a.signature <= keys]
    if not hits:
        raise Unrecognised("its keys match no kicad-tools finding model")
    best = max(len(a.signature) for a in hits)
    top = [a for a in hits if len(a.signature) == best]
    if len(top) > 1:
        raise Unrecognised(
            "it matches more than one kicad-tools model equally", [a.qualname for a in top]
        )
    return top[0]


def locators(finding: Any, adapter: Adapter) -> list[Locator]:
    if isinstance(finding, dict):

        def get(name: str) -> Any:
            return finding.get(name)
    else:

        def get(name: str) -> Any:
            return getattr(finding, name, None)

    return adapter.extract(get, adapter.module_obj)
