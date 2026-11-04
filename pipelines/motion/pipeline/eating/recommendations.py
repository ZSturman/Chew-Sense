"""Turn chewing-bout errors and coverage gaps into recording suggestions.

Suggestions name existing guided experiments (``pipelines/vision/configs/
experiments``) where one fits.  They are heuristics for a single-wearer pilot:
a context is flagged when the selected model's false-positive rate there is
high, when its recall is low, or when too few labelled windows exist to say
anything about it.
"""

from __future__ import annotations

from typing import Any, Mapping

# Activity context (as produced by dataset._context_at) -> guided experiment.
CONTEXT_EXPERIMENTS = {
    "guided:talk": ("talking", "Talking without eating"),
    "field:nodding": ("head-movement", "Head movement"),
    "field:head_shake": ("head-movement", "Head movement"),
    "guided:head_movement": ("head-movement", "Head movement"),
    "guided:sync_marker": ("head-movement", "Head movement"),
    "field:walking": ("walking-no-food", "Walking without eating"),
    "guided:walk": ("walking-no-food", "Walking without eating"),
    "guided:drink": ("drinking", "Drinking"),
    "meal_legacy": ("baseline-90s", "Chewing with pauses (stop/start transitions)"),
    "guided:stop_chewing": ("baseline-90s", "Chewing with pauses (stop/start transitions)"),
    "guided:rest": ("quiet-sitting", "Quiet sitting"),
    "field:eating_hard": ("crunchy-food", "Crunchy food"),
    "field:eating_soft": ("soft-food", "Soft food"),
    "guided:other": ("facial-expressions", "Smiling, yawning and expressions"),
}
# Confounds every eating detector must see before its false-positive rate means much.
REQUIRED_CONFOUNDS = {
    "talking": ("talking", "Talking without eating", ("guided:talk",)),
    "drinking": ("drinking", "Drinking", ("guided:drink",)),
    "walking": ("walking-no-food", "Walking without eating", ("field:walking", "guided:walk")),
    "head movement": ("head-movement", "Head movement", ("field:nodding", "field:head_shake", "guided:head_movement")),
}
MIN_CONTEXT_WINDOWS = 60
MIN_SHARED_CLOCK_CHEWING_WINDOWS = 300


def _combined_contexts(result: Mapping[str, Any]) -> dict[str, dict[str, int]]:
    combined: dict[str, dict[str, int]] = {}
    for split in ("dev_oof", "test"):
        metrics = result.get(split) or {}
        for context, values in (metrics.get("by_context") or {}).items():
            entry = combined.setdefault(context, {"tp": 0, "fp": 0, "tn": 0, "fn": 0, "windows": 0})
            for key in entry:
                entry[key] += int(values.get(key) or 0)
    return combined


def collection_recommendations(summary: Mapping[str, Any]) -> list[dict[str, Any]]:
    selected = summary.get("selected_model")
    if not selected:
        return []
    result = summary["results"][selected]
    contexts = _combined_contexts(result)
    items: list[dict[str, Any]] = []

    def add(priority: float, title: str, reason: str, experiment_id: str | None) -> None:
        for existing in items:
            if existing["experiment_id"] == experiment_id and experiment_id is not None:
                existing["reason"] += f" Also: {reason}"
                existing["priority"] = max(existing["priority"], priority)
                return
        items.append({"priority": priority, "title": title, "reason": reason, "experiment_id": experiment_id})

    for context, c in contexts.items():
        negatives = c["fp"] + c["tn"]
        positives = c["tp"] + c["fn"]
        mapped = CONTEXT_EXPERIMENTS.get(context)
        if negatives >= 10 and c["fp"] / negatives > 0.05:
            rate = c["fp"] / negatives
            add(
                2.0 + rate,
                mapped[1] if mapped else f"More non-eating examples like '{context}'",
                f"{rate:.0%} of {negatives} non-chewing windows in '{context}' were flagged as chewing.",
                mapped[0] if mapped else None,
            )
        if positives >= 10 and c["tp"] / positives < 0.8:
            recall = c["tp"] / positives
            add(
                2.0 + (1 - recall),
                mapped[1] if mapped else f"More chewing examples like '{context}'",
                f"Only {recall:.0%} of {positives} chewing windows in '{context}' were detected.",
                mapped[0] if mapped else None,
            )
    for name, (experiment_id, title, keys) in REQUIRED_CONFOUNDS.items():
        windows = sum(contexts.get(key, {}).get("windows", 0) for key in keys)
        if windows < MIN_CONTEXT_WINDOWS:
            add(
                1.5,
                title,
                f"Only {windows} labelled windows of {name}; too few to estimate its false-positive rate.",
                experiment_id,
            )
    shared_chewing = 0
    for split in ("dev_oof", "test"):
        tier = ((result.get(split) or {}).get("by_tier") or {}).get("human_coarse") or {}
        shared_chewing += int(tier.get("positives") or 0)
    if shared_chewing < MIN_SHARED_CLOCK_CHEWING_WINDOWS:
        add(
            1.8,
            "Guided chewing with a verified clock",
            f"Only {shared_chewing} chewing windows come from recordings with a shared video/motion clock; "
            "most positives are 2025 labels whose alignment cannot be checked.",
            "baseline-90s",
        )
    add(
        1.0,
        "A natural meal (manual Eating capture, then mark chewing spans)",
        "No natural, uninstructed meal longer than five minutes exists; guided takes cannot show "
        "real-world bout lengths, pauses or conversation during eating.",
        None,
    )
    recorded = {
        session.get("activity", "").removeprefix("guided:")
        for session in summary.get("sessions_overview", ())
    }
    for experiment_id, title, missing in (
        ("talking-while-eating", "Talking while eating", "No recording mixes chewing with speech, as real meals do."),
        ("gum-chewing", "Chewing gum", "Gum is chewing without eating; whether motion can separate it is untested."),
        ("chewing-pace", "Slow and fast chewing", "All chewing is at one self-selected pace; cadence work needs a range."),
        ("facial-expressions", "Smiling, yawning and expressions", "Non-eating mouth movements are absent as negatives."),
    ):
        if experiment_id not in recorded:
            add(0.9, title, missing, experiment_id)
    return sorted(items, key=lambda item: -item["priority"])
