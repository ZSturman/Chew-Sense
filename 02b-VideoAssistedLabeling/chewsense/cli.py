"""Click-based CLI."""
from __future__ import annotations

import json
from pathlib import Path

import click

from .config import load_config
from .pipeline import run as run_pipeline
from .session_bundle import finalize_session, migrate_legacy_session, migrate_tree


@click.group()
def cli():
    """ChewSense — video chew & eating-episode labeller."""


@cli.command()
@click.argument("video", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.option("--out", "out_dir", type=click.Path(path_type=Path), required=True,
              help="Output directory.")
@click.option("--config", "config_path", type=click.Path(exists=True, dir_okay=False, path_type=Path),
              default=None, help="Optional config YAML to override defaults.")
@click.option("--user", "user_id", type=str, default=None,
              help="User id for per-user baselines (default: from config).")
def run(video: Path, out_dir: Path, config_path: Path | None, user_id: str | None):
    """Run the pipeline on VIDEO and write outputs to --out."""
    cfg = load_config(config_path)
    out_dir.mkdir(parents=True, exist_ok=True)
    events = run_pipeline(video, out_dir, cfg, user_id=user_id)
    q = events.quality
    line = (f"done: {len(events.episodes)} episodes, "
            f"{sum(e.chew_count for e in events.episodes)} chews, "
            f"{sum(e.bite_count for e in events.episodes)} bites")
    if q is not None:
        cpb = q.session.mean_chews_per_bite
        rate = q.session.mean_chew_rate_hz
        line += f" | mean_cpb={cpb:.1f}" if cpb is not None else ""
        line += f" | mean_rate={rate:.2f}Hz" if rate is not None else ""
        if q.session.flags:
            line += f" | flags={','.join(q.session.flags)}"
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


@cli.command("pseudolabel")
@click.argument("run_dir", type=click.Path(exists=True, file_okay=False, path_type=Path))
@click.option("--out", "out_path", type=click.Path(path_type=Path), required=True,
              help="Output labels.parquet path.")
@click.option(
    "--annotations",
    "annotations_path",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    default=None,
    help="Verified canonical_events.json; uses human review instead of pseudo-labels.",
)
def pseudolabel_cmd(run_dir: Path, out_path: Path, annotations_path: Path | None):
    """Build per-frame labels from a run, preferring verified review JSON."""
    from .training.pseudolabel import write_labels
    p = write_labels(run_dir, out_path, annotations_path=annotations_path)
    click.echo(f"labels -> {p}")


@cli.command("train-chew-head")
@click.option("--pair", "pairs", multiple=True, required=True,
              help="features.parquet:labels.parquet (repeatable).")
@click.option("--out", "out_path", type=click.Path(path_type=Path), required=True,
              help="Where to write the .pt checkpoint.")
@click.option("--config", "config_path", type=click.Path(exists=True, dir_okay=False, path_type=Path),
              default=None)
@click.option("--epochs", type=int, default=20)
@click.option("--batch-size", type=int, default=256)
@click.option("--lr", type=float, default=3e-3)
def train_chew_head_cmd(pairs, out_path: Path, config_path: Path | None,
                        epochs: int, batch_size: int, lr: float):
    """Train the chew-head classifier on (features, pseudo-labels) pairs."""
    from .training.train import train as train_fn
    cfg = load_config(config_path)
    parsed: list[tuple[Path, Path]] = []
    for p in pairs:
        if ":" not in p:
            raise click.BadParameter(f"bad --pair: {p} (expected features:labels)")
        f, l = p.split(":", 1)
        parsed.append((Path(f), Path(l)))
    summary = train_fn(parsed, Path(out_path), cfg,
                       epochs=epochs, batch_size=batch_size, lr=lr)
    click.echo(f"trained on {summary['n_train']} windows, "
               f"final val_acc={summary['history'][-1]['val_acc']:.3f} -> {summary['out_path']}")


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
@click.option("--allow-incomplete", is_flag=True,
              help="Materialize an in-review revision without admitting it to training.")
def finalize_session_cmd(session_dir: Path, derived_dir: Path, allow_incomplete: bool):
    """Create an immutable reviewed revision and both training manifests."""
    annotations = finalize_session(
        session_dir,
        derived_dir,
        require_verified=not allow_incomplete,
    )
    click.echo(
        f"finalized {annotations.session_id} revision={annotations.revision} "
        f"verified={annotations.verified}"
    )


if __name__ == "__main__":
    cli()
