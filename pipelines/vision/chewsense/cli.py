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
    from .pipeline import run as run_pipeline

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


if __name__ == "__main__":
    cli()
