"""Authoritative override merger.

Applies a ``review_overrides.json`` payload on top of an auto-labelled
``motion_aligned_labels.csv`` and writes the merged CSV back.

This implementation is the **source of truth**; the Swift `OverrideApplier` in
``04-RealWorldTesting/ChewSense-CycleReviewer/`` mirrors the same logic for the
GUI's live preview, but every CI / pipeline run uses this script.
"""
from __future__ import annotations

import argparse
import json
import logging
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Iterable

import pandas as pd


logger = logging.getLogger(__name__)

# Minimum spacing between adjacent chews. ~1 frame at 30 fps. Mirrors the
# `ReviewerState.neighborGap` constant in the Swift UI so the two layers
# enforce the same invariant.
NEIGHBOR_GAP_S = 1.0 / 30.0


@dataclass
class ChewEdit:
    id: str
    start_ts: float
    peak_ts: float
    end_ts: float
    confidence: float = 1.0

    @classmethod
    def from_dict(cls, d: dict) -> "ChewEdit":
        return cls(
            id=d["id"],
            start_ts=float(d["start_ts"]),
            peak_ts=float(d["peak_ts"]),
            end_ts=float(d["end_ts"]),
            confidence=float(d.get("confidence", 1.0)),
        )


@dataclass
class TimeSpan:
    start_ts: float
    end_ts: float

    @classmethod
    def from_dict(cls, d: dict) -> "TimeSpan":
        return cls(start_ts=float(d["start_ts"]), end_ts=float(d["end_ts"]))


@dataclass
class BiteEdit:
    id: str
    ts: float
    confidence: float = 1.0

    @classmethod
    def from_dict(cls, d: dict) -> "BiteEdit":
        return cls(
            id=d["id"],
            ts=float(d["ts"]),
            confidence=float(d.get("confidence", 1.0)),
        )


@dataclass
class ReviewOverrides:
    session_id: str
    verified: bool = False
    reviewer: str | None = None
    reviewed_at: str | None = None
    added_chews: list[ChewEdit] = field(default_factory=list)
    deleted_chew_ids: list[str] = field(default_factory=list)
    edited_chews: list[ChewEdit] = field(default_factory=list)
    flipped_eating_spans: list[TimeSpan] = field(default_factory=list)
    unreliable_spans: list[TimeSpan] = field(default_factory=list)
    verified_chew_ids: list[str] = field(default_factory=list)
    added_bites: list[BiteEdit] = field(default_factory=list)
    deleted_bite_ids: list[str] = field(default_factory=list)
    edited_bites: list[BiteEdit] = field(default_factory=list)
    verified_bite_ids: list[str] = field(default_factory=list)
    full_video_pass_completed: bool = False
    notes: str | None = None

    @classmethod
    def load(cls, path: Path) -> "ReviewOverrides":
        data = json.loads(path.read_text())
        return cls(
            session_id=data["session_id"],
            verified=bool(data.get("verified", False)),
            reviewer=data.get("reviewer"),
            reviewed_at=data.get("reviewed_at"),
            added_chews=[ChewEdit.from_dict(c) for c in data.get("added_chews", [])],
            deleted_chew_ids=list(data.get("deleted_chew_ids", [])),
            edited_chews=[ChewEdit.from_dict(c) for c in data.get("edited_chews", [])],
            flipped_eating_spans=[TimeSpan.from_dict(s) for s in data.get("flipped_eating_spans", [])],
            unreliable_spans=[TimeSpan.from_dict(s) for s in data.get("unreliable_spans", [])],
            verified_chew_ids=list(data.get("verified_chew_ids", [])),
            added_bites=[BiteEdit.from_dict(b) for b in data.get("added_bites", [])],
            deleted_bite_ids=list(data.get("deleted_bite_ids", [])),
            edited_bites=[BiteEdit.from_dict(b) for b in data.get("edited_bites", [])],
            verified_bite_ids=list(data.get("verified_bite_ids", [])),
            full_video_pass_completed=bool(
                data.get("full_video_pass_completed", data.get("verified", False))
            ),
            notes=data.get("notes"),
        )


def _repair_overlaps(
    chews: list[ChewEdit],
    *,
    gap: float = NEIGHBOR_GAP_S,
) -> list[ChewEdit]:
    """Return a copy of ``chews`` with no temporal overlap.

    A chew may start at ``previous.end_ts + gap`` but never earlier. If two
    chews overlap, the *later*-starting one is trimmed forward; if that would
    invalidate it (peak no longer between start and end, or duration would
    become non-positive), it is dropped with a warning. This mirrors the
    Swift `ReviewerState.trimNeighborToFit` behaviour for the offline merger.
    """
    if not chews:
        return []
    ordered = sorted(chews, key=lambda c: c.start_ts)
    repaired: list[ChewEdit] = []
    for c in ordered:
        if not repaired:
            repaired.append(c)
            continue
        prev = repaired[-1]
        if c.start_ts >= prev.end_ts + gap:
            repaired.append(c)
            continue
        new_start = prev.end_ts + gap
        if new_start >= c.end_ts or new_start >= c.peak_ts:
            logger.warning(
                "chew %s overlaps %s irreparably (start %.4f → %.4f exceeds peak %.4f / end %.4f); dropping",
                c.id, prev.id, c.start_ts, new_start, c.peak_ts, c.end_ts,
            )
            continue
        logger.warning(
            "chew %s overlaps %s; trimming start_ts %.4f → %.4f",
            c.id, prev.id, c.start_ts, new_start,
        )
        repaired.append(replace(c, start_ts=new_start))
    return repaired


def apply_overrides(rows: pd.DataFrame, overrides: ReviewOverrides) -> pd.DataFrame:
    """Return a new DataFrame with overrides applied.

    Same column schema as the input: timestamp, video_ts, eating, chew_phase,
    chew_id, video_confidence. The input is treated read-only.

    Overlap invariant: a chew's ``start_ts`` is always ≥ the previous chew's
    ``end_ts + NEIGHBOR_GAP_S``. Reviewer-supplied chews that violate this are
    auto-trimmed (or dropped if they cannot be repaired) with a logged warning.
    """
    df = rows.copy()
    required = {"timestamp", "video_ts", "eating", "chew_phase", "chew_id", "video_confidence"}
    missing = required - set(df.columns)
    if missing:
        raise ValueError(f"missing columns: {sorted(missing)}")

    # Empty CSV cells round-trip as NaN; coerce string cols so assignments work.
    df["chew_id"] = df["chew_id"].fillna("").astype(str)
    df["chew_phase"] = df["chew_phase"].fillna("idle").astype(str)
    df["eating"] = df["eating"].fillna(0).astype(int)

    # 1) Delete chews — wipe phase/id within affected rows.
    if overrides.deleted_chew_ids:
        mask = df["chew_id"].isin(overrides.deleted_chew_ids)
        df.loc[mask, "chew_id"] = ""
        df.loc[mask, "chew_phase"] = "idle"

    # 2) Apply edited + added chews. Repair overlaps among the reviewer-supplied
    #    chews before stamping rows, so an edited+added pair that crosses a
    #    boundary in the JSON cannot land in the merged CSV with overlap.
    review_chews = _repair_overlaps(list(overrides.edited_chews) + list(overrides.added_chews))
    for chew in review_chews:
        # Clear any rows still tagged with this id before re-stamping.
        same_id = df["chew_id"] == chew.id
        df.loc[same_id, "chew_id"] = ""
        df.loc[same_id, "chew_phase"] = "idle"

        opening = (df["video_ts"] >= chew.start_ts) & (df["video_ts"] <= chew.peak_ts)
        closing = (df["video_ts"] > chew.peak_ts) & (df["video_ts"] <= chew.end_ts)
        df.loc[opening, "chew_id"] = chew.id
        df.loc[opening, "chew_phase"] = "opening"
        df.loc[opening, "video_confidence"] = chew.confidence
        df.loc[closing, "chew_id"] = chew.id
        df.loc[closing, "chew_phase"] = "closing"
        df.loc[closing, "video_confidence"] = chew.confidence

    # 3) Flip eating in user-marked spans.
    for span in overrides.flipped_eating_spans:
        m = (df["video_ts"] >= span.start_ts) & (df["video_ts"] <= span.end_ts)
        df.loc[m, "eating"] = 1 - df.loc[m, "eating"].astype(int)

    # 4) Mark unreliable spans (overrides everything else within the span).
    for span in overrides.unreliable_spans:
        m = (df["video_ts"] >= span.start_ts) & (df["video_ts"] <= span.end_ts)
        df.loc[m, "chew_phase"] = "unreliable"
        df.loc[m, "chew_id"] = ""

    if {"bite_event", "bite_id"} <= set(df.columns):
        if overrides.deleted_bite_ids:
            m = df["bite_id"].fillna("").isin(overrides.deleted_bite_ids)
            df.loc[m, "bite_event"] = 0
            df.loc[m, "bite_id"] = ""
        for bite in list(overrides.edited_bites) + list(overrides.added_bites):
            same_id = df["bite_id"].fillna("") == bite.id
            df.loc[same_id, "bite_event"] = 0
            df.loc[same_id, "bite_id"] = ""
            nearest = (df["video_ts"].astype(float) - bite.ts).abs().idxmin()
            df.loc[nearest, "bite_event"] = 1
            df.loc[nearest, "bite_id"] = bite.id
            df.loc[nearest, "video_confidence"] = bite.confidence
    if overrides.verified and "label_source" in df.columns:
        df["label_source"] = "human_reviewed"

    return df


def merge_session(
    aligned_csv: Path,
    overrides_json: Path,
    *,
    snapshot_auto: bool = True,
) -> dict:
    """Read aligned CSV + overrides, write merged CSV back. Returns summary."""
    df = pd.read_csv(aligned_csv)
    overrides = ReviewOverrides.load(overrides_json)

    if snapshot_auto:
        snap = aligned_csv.with_name("motion_aligned_labels.auto.csv")
        if not snap.exists():
            snap.write_text(aligned_csv.read_text())

    merged = apply_overrides(df, overrides)
    merged.to_csv(aligned_csv, index=False)

    return {
        "session_id": overrides.session_id,
        "verified": overrides.verified,
        "n_rows": int(len(merged)),
        "n_eating_rows": int(merged["eating"].sum()),
        "n_chew_rows": int((merged["chew_id"] != "").sum()),
        "n_unreliable_rows": int((merged["chew_phase"] == "unreliable").sum()),
        "n_added": len(overrides.added_chews),
        "n_deleted": len(overrides.deleted_chew_ids),
        "n_edited": len(overrides.edited_chews),
        "n_added_bites": len(overrides.added_bites),
        "n_deleted_bites": len(overrides.deleted_bite_ids),
        "n_edited_bites": len(overrides.edited_bites),
        "full_video_pass_completed": overrides.full_video_pass_completed,
    }


def iter_sessions(derived_root: Path, raw_root: Path) -> Iterable[tuple[Path, Path]]:
    for d in sorted(derived_root.iterdir()):
        if not d.is_dir():
            continue
        aligned = d / "motion_aligned_labels.csv"
        overrides = raw_root / d.name / "review_overrides.json"
        if aligned.exists() and overrides.exists():
            yield aligned, overrides


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Apply review overrides to aligned label CSVs.")
    p.add_argument("--derived-root", type=Path, required=True,
                   help="02-DataPipeline/data/derived/video_labels")
    p.add_argument("--raw-root", type=Path, required=True,
                   help="02-DataPipeline/data/raw_sessions")
    p.add_argument("--only", action="append", default=None, help="Restrict to these session ids")
    p.add_argument("--no-snapshot", action="store_true",
                   help="Do not snapshot the pre-merge CSV to motion_aligned_labels.auto.csv")
    args = p.parse_args(argv)

    processed = 0
    for aligned, overrides in iter_sessions(args.derived_root, args.raw_root):
        sid = aligned.parent.name
        if args.only and sid not in args.only:
            continue
        summary = merge_session(aligned, overrides, snapshot_auto=not args.no_snapshot)
        processed += 1
        print(f"[merged] {sid}: {summary}")
    print(f"\nsummary: processed={processed}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
