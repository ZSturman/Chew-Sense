"""ML refinement hook for mouth-cycle kind classification.

Phase 1 ships a heuristic cycle classifier inside
:mod:`signals.mouth_state`. This module exists as a **drop-in seam** for a
small classifier trained in Phase 2 to refine visible-open / closed-lip /
ambiguous predictions (especially distinguishing closed-lip teeth-separation
from talking-only motion). Today it is intentionally a no-op: it accepts the
evidence dict and returns ``(None, None)`` meaning "keep the heuristic
label".

When Phase 2 brings a trained model, replace :func:`predict_kind` to:

1. Pull the relevant scalars from ``evidence`` (z-scores, durations,
   pose) plus any feature window the model expects.
2. Return ``(kind, confidence)`` where ``kind`` is one of
   ``"visible_open" | "closed_lip" | "ambiguous"``.

Returning ``(None, None)`` is always a safe fallback.
"""
from __future__ import annotations

from typing import Optional

from ..events.schema import MouthCycleKind


def predict_kind(evidence: dict, cfg: dict) -> tuple[Optional[MouthCycleKind], Optional[float]]:
    """Return ``(kind, confidence)`` or ``(None, None)`` to keep the heuristic.

    Phase-1 stub: pass-through.
    """
    if not cfg.get("cycle", {}).get("ml_refinement_enabled", False):
        return None, None
    # Placeholder for a trained model. Echo the heuristic label so the
    # contract stays stable for Phase 2.
    return evidence.get("heuristic_kind"), None
