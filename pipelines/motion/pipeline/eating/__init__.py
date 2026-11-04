"""AirPods-motion chewing-bout detection track.

This package trains and evaluates *motion-only* detectors of human-labelled
chewing bouts.  It exists because the schema-v2 temporal path requires
individually reviewed chew events before any model can train, while the
workspace already holds human coarse chewing intervals.  Labels here are
coarse (a span the wearer marked as chewing), never individual chews, and
every labelled second keeps the provenance tier it came from.

Source recordings are opened read-only.  Nothing in this package writes into
session folders, the loop service tree, or the model registry.
"""

from .dataset import (
    EVALUATION_TIERS,
    LABEL_TIERS,
    EatingSession,
    LabeledInterval,
    SessionRejected,
    WindowSet,
    build_windows,
    discover_sessions,
)

__all__ = [
    "EVALUATION_TIERS",
    "LABEL_TIERS",
    "EatingSession",
    "LabeledInterval",
    "SessionRejected",
    "WindowSet",
    "build_windows",
    "discover_sessions",
]
