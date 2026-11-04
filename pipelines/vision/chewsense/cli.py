"""Click-based CLI."""
from __future__ import annotations

import json
from pathlib import Path

import click

from .config import load_config
from .session_bundle import (
    audit_session,
    create_candidates_from_events,
    finalize_session,
    migrate_legacy_session,
    migrate_tree,
)


@click.group()
def cli():
    """ChewSense — video chew & eating-episode labeller."""


@cli.command()
@click.argument("video", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.option("--out", "out_dir", type=click.Path(path_type=Path), required=True,
              help="Output directory.")
@click.option("--config", "config_path", type=click.Path(exists=True, dir_okay=False, path_type=Path),
              default=None, help="Optional config YAML to override defaults.")
def run(video: Path, out_dir: Path, config_path: Path | None):
    """Run the pipeline on VIDEO and write outputs to --out."""
    from .features.offline import reexec_offline
    from .pipeline import run as run_pipeline

    reexec_offline()

    cfg = load_config(config_path)
    out_dir.mkdir(parents=True, exist_ok=True)
    events = run_pipeline(video, out_dir, cfg)
    line = (f"done: {len(events.mouth_cycles)} chew-cycle proposals, "
            f"{len(events.food_entry_proposals)} food-entry proposals")
    click.echo(f"{line} -> {out_dir}")


@cli.command("extract-features")
@click.argument("video", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.option("--out", "out_path", type=click.Path(path_type=Path), required=True,
              help="Output features.parquet path.")
@click.option("--config", "config_path", type=click.Path(exists=True, dir_okay=False, path_type=Path),
              default=None)
def extract_features_cmd(video: Path, out_path: Path, config_path: Path | None):
    """Extract per-frame feature parquet from VIDEO (no detection)."""
    from .features.extract import write_features
    from .features.offline import reexec_offline

    reexec_offline()
    cfg = load_config(config_path)
    p = write_features(video, out_path, cfg)
    click.echo(f"features -> {p}")


@cli.command("migrate-session")
@click.argument("session_dir", type=click.Path(exists=True, file_okay=False, path_type=Path))
@click.option("--wearer", "wearer_id", default="local-user", show_default=True)
@click.option("--source-app", type=click.Choice(["collect", "live", "legacy_import"]),
              default="legacy_import", show_default=True)
@click.option("--activity", default=None)
@click.option("--force", is_flag=True, help="Replace an existing generated session.json.")
def migrate_session_cmd(
    session_dir: Path,
    wearer_id: str,
    source_app: str,
    activity: str | None,
    force: bool,
):
    """Add the canonical bundle metadata to one existing session."""
    manifest = migrate_legacy_session(
        session_dir,
        wearer_id=wearer_id,
        source_app=source_app,  # type: ignore[arg-type]
        activity=activity,
        force=force,
    )
    click.echo(f"migrated {manifest.session_id} -> {session_dir / 'session.json'}")


@cli.command("migrate-tree")
@click.argument("input_root", type=click.Path(exists=True, file_okay=False, path_type=Path))
@click.option("--wearer", "wearer_id", default="local-user", show_default=True)
@click.option("--force", is_flag=True)
def migrate_tree_cmd(input_root: Path, wearer_id: str, force: bool):
    """Add canonical metadata to every session folder under INPUT_ROOT."""
    result = migrate_tree(input_root, wearer_id=wearer_id, force=force)
    click.echo(json.dumps(result, indent=2))


@cli.command("finalize-session")
@click.argument("session_dir", type=click.Path(exists=True, file_okay=False, path_type=Path))
@click.option("--derived", "derived_dir",
              type=click.Path(exists=True, file_okay=False, path_type=Path), required=True)
def finalize_session_cmd(session_dir: Path, derived_dir: Path):
    """Create an immutable reviewed revision and both training manifests."""
    annotations = finalize_session(
        session_dir,
        derived_dir,
    )
    click.echo(
        f"finalized {annotations.session_id} revision={annotations.review_revision} "
        f"verified={annotations.verified}"
    )


@cli.command("generate-candidates")
@click.argument("session_dir", type=click.Path(exists=True, file_okay=False, path_type=Path))
@click.option(
    "--derived",
    "derived_dir",
    type=click.Path(exists=True, file_okay=False, path_type=Path),
    required=True,
)
def generate_candidates_cmd(session_dir: Path, derived_dir: Path):
    """Create an immutable candidate revision from automatic telemetry."""
    from .integrations.export_to_pipeline import load_events_json

    events = load_events_json(derived_dir / "events.json")
    path, artifact = create_candidates_from_events(session_dir, derived_dir, events)
    click.echo(
        f"candidates {artifact.session_id} revision={artifact.candidate_revision} "
        f"sha256={artifact.content_sha256} -> {path}"
    )


@cli.command("audit-session")
@click.argument("session_dir", type=click.Path(exists=True, file_okay=False, path_type=Path))
@click.option(
    "--derived",
    "derived_dir",
    type=click.Path(exists=True, file_okay=False, path_type=Path),
    default=None,
    help="Vision output directory containing immutable candidates.",
)
def audit_session_cmd(session_dir: Path, derived_dir: Path | None):
    """Read-only validation of a session's clocks, hashes, IDs, and lineage."""
    if derived_dir is None:
        from .paths import derived_vision_root

        inferred = derived_vision_root() / session_dir.name
        derived_dir = inferred if inferred.exists() else None
    report = audit_session(session_dir, derived_dir)
    click.echo(json.dumps(report.model_dump(mode="json"), indent=2))
    if not report.ok:
        raise click.ClickException("session audit failed")



@cli.command("prepare-experiment")
@click.argument("source", type=click.Path(exists=True, file_okay=False, path_type=Path))
@click.option("--workspace", type=click.Path(path_type=Path), required=True)
@click.option("--blind", is_flag=True, help="Create an empty candidate artifact; no model output is copied.")
def prepare_experiment_cmd(source: Path, workspace: Path, blind: bool):
    """Create a new working copy with checksum-verified media; never overwrite."""
    from .eval.phase0 import prepare_experiment
    click.echo(prepare_experiment(source, workspace.resolve(), blind=blind))


@cli.command("audit-timing")
@click.argument("session", type=click.Path(exists=True, file_okay=False, path_type=Path))
@click.option("--markers", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.option("--out", type=click.Path(path_type=Path), required=True)
def audit_timing_cmd(session: Path, markers: Path | None, out: Path):
    """Audit display PTS, motion coverage and optional independent sync markers."""
    from .eval.phase0 import timing_report
    report = timing_report(session, json.loads(markers.read_text()) if markers else None)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("x") as handle:
        json.dump(report, handle, indent=2, allow_nan=False)
    click.echo(out)


@cli.command("evaluate-experiment")
@click.option("--predictions", type=click.Path(exists=True, path_type=Path), required=True)
@click.option("--gold-review", type=click.Path(exists=True, path_type=Path), required=True)
@click.option("--gold-candidates", type=click.Path(exists=True, path_type=Path), required=True)
@click.option("--assisted-review", type=click.Path(exists=True, path_type=Path))
@click.option("--tolerance", type=float, default=0.25, show_default=True)
@click.option("--out", type=click.Path(path_type=Path), required=True)
def evaluate_experiment_cmd(predictions, gold_review, gold_candidates, assisted_review, tolerance, out):
    """Evaluate predictions against independently frozen blind annotations."""
    from .eval.phase0 import evaluate_experiment
    result = evaluate_experiment(predictions, gold_review, gold_candidates,
                                 tolerance_s=tolerance, assisted_review_path=assisted_review)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("x") as handle:
        json.dump(result, handle, indent=2, allow_nan=False)
    click.echo(out)


@cli.command("review-coarse")
@click.argument("session", type=click.Path(exists=True, file_okay=False, path_type=Path))
@click.option("--chewing", multiple=True, help="Reviewed start:end seconds; repeat for each chewing span.")
@click.option("--unknown", multiple=True, help="Uncertain start:end seconds; repeat as needed.")
@click.option("--reviewer", required=True)
@click.option("--full-video-pass", is_flag=True, required=True, help="Explicitly confirm the whole video was reviewed.")
def review_coarse_cmd(session, chewing, unknown, reviewer, full_video_pass):
    """Save an explicit full-video coarse review; remaining time is not_chewing.

    Use on a fresh experiment copy BEFORE generating any candidates.
    """
    from .ingest import probe
    from .session_bundle import load_session_manifest
    manifest = load_session_manifest(session)
    if not manifest.video:
        raise click.ClickException("coarse review requires video")
    derived = session.parents[2] / "derived/vision" / manifest.session_id
    if any(derived.glob("candidates-v*.json")):
        raise click.ClickException("candidates already exist; use a fresh experiment copy for changed coarse review")
    duration = probe(session / manifest.video.path).duration_s
    spans = []
    for label, values in [("chewing", chewing), ("unknown", unknown)]:
        for value in values:
            a, b = map(float, value.split(":"))
            if not (0 <= a < b <= duration):
                raise click.ClickException("range must satisfy 0 <= start < end <= video duration")
            spans.append((a, b, label))
    intervals, previous = [], 0.0
    for a, b, label in sorted(spans):
        if a < previous:
            raise click.ClickException("coarse ranges overlap")
        if a > previous:
            intervals.append(dict(start_ts=previous, end_ts=a, label="not_chewing", source="human_coarse"))
        intervals.append(dict(start_ts=a, end_ts=b, label=label, source="human_coarse"))
        previous = b
    if previous < duration:
        intervals.append(dict(start_ts=previous, end_ts=duration, label="not_chewing", source="human_coarse"))
    annotations = session / "annotations"
    annotations.mkdir(exist_ok=True)
    payload = dict(schema_version="2.0.0", artifact_type="coarse_labels", session_id=manifest.session_id,
                   reviewer=reviewer, full_video_pass_completed=True, intervals=intervals)
    snapshot = annotations / f"coarse-review-v{len(list(annotations.glob('coarse-review-v*.json'))) + 1:04d}.json"
    with snapshot.open("x") as handle:
        json.dump(payload, handle, indent=2)
    temporary = annotations / ".coarse_labels.tmp"
    temporary.write_text(json.dumps(payload, indent=2))
    temporary.replace(annotations / "coarse_labels.json")
    click.echo(snapshot)


@cli.command("init-blind-review")
@click.argument("session", type=click.Path(exists=True, file_okay=False, path_type=Path))
@click.option("--derived", type=click.Path(path_type=Path), required=True)
def init_blind_review_cmd(session, derived):
    """Initialize empty immutable proposals after coarse review; never run CV here."""
    from .eval.phase0 import init_blind_review
    click.echo(init_blind_review(session, derived))


from .loop.cli import loop as _loop  # noqa: E402

cli.add_command(_loop)

if __name__ == "__main__":
    cli()
