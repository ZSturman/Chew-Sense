"""``chewsense-vision loop …`` commands. Day-to-day use needs only ``serve``
(or the LaunchAgent from ``install-agent``); the rest are fallbacks and tools.
"""
from __future__ import annotations

import json
import os
import plistlib
import signal
import sys
import threading
import urllib.request
from pathlib import Path

import click

from .config import load_loop_config
from .experiments import SEED_DIR, ExperimentDefinition, ExperimentRegistry, build_catalog
from .store import LoopPaths, read_json

AGENT_LABEL = "com.chewsense.loop"


def _paths() -> LoopPaths:
    return LoopPaths.default().ensure()


def _config(paths: LoopPaths) -> dict:
    return load_loop_config(paths.service / "loop.yaml")


def _admin(paths: LoopPaths, method: str, route: str, body: dict | None = None) -> dict:
    endpoint = read_json(paths.service / "endpoint.json")
    if not endpoint:
        raise click.ClickException("the loop service is not running; start it with `loop serve`")
    token = (paths.service / "admin.token").read_text().strip()
    request = urllib.request.Request(
        f"http://127.0.0.1:{endpoint['admin_port']}{route}", method=method,
        data=json.dumps(body or {}).encode() if method == "POST" else None,
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.loads(response.read())
    except OSError as exc:
        raise click.ClickException(f"cannot reach the loop service: {exc}") from exc


@click.group("loop")
def loop():
    """Guided collection, automatic processing and evaluation loop."""


@loop.command()
@click.option("--host", default="0.0.0.0", show_default=True, help="Device API bind address.")
@click.option("--no-bonjour", is_flag=True, help="Do not advertise on the local network.")
def serve(host: str, no_bonjour: bool):
    """Run the Mac service in the foreground until interrupted."""
    from .server import LoopService, serve as start

    paths = _paths()
    service = LoopService(paths, _config(paths))
    device, admin, stop = start(service, host=host, advertise=not no_bonjour)
    click.echo(f"ChewSense loop service on port {device.server_address[1]} "
               f"(admin 127.0.0.1:{admin.server_address[1]})")
    click.echo(f"Certificate fingerprint: {service.identity.fingerprint}")
    done = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: done.set())
    try:
        done.wait()
    except KeyboardInterrupt:
        pass
    stop.set()
    service.training_stop.set()
    if hasattr(service.processor.vision, "terminate"):
        service.processor.vision.terminate()
    device.shutdown()
    admin.shutdown()


@loop.command()
def pair():
    """Open a pairing window and print the code to enter on the iPhone."""
    window = _admin(_paths(), "POST", "/admin/pairing")
    click.echo(f"Pairing code: {window['code']} (valid {int(window['expires_in_s'] // 60)} min)")
    click.echo(f"The iPhone should show fingerprint {window['fingerprint'][:16]}…")


@loop.command()
def status():
    """Print processing status and the latest analysis summary."""
    paths = _paths()
    click.echo(json.dumps({"status": read_json(paths.status, {}),
                           "review_tasks": len((read_json(paths.review_tasks, {}) or {}).get("tasks", []))},
                          indent=2))


@loop.command("import")
@click.argument("archive", type=click.Path(exists=True, dir_okay=False, path_type=Path))
def import_cmd(archive: Path):
    """Queue an AirDropped Collect ZIP exactly as if it had been uploaded."""
    import shutil
    import uuid

    from .importer import sha256_file

    paths = _paths()
    digest = sha256_file(archive)
    staged = paths.uploads / uuid.uuid4().hex / "data.zip"
    staged.parent.mkdir(parents=True)
    shutil.copy2(archive, staged)
    from .processing import enqueue_job

    job = enqueue_job(paths, staged, archive_sha256=digest, device="manual")
    click.echo(f"queued job {job['job_id']}; the running service will process it")


@loop.command()
def analyze():
    """Run evaluation, weakness analysis and planning now."""
    paths = _paths()
    try:
        report = _admin(paths, "POST", "/admin/analyze")
    except click.ClickException:
        from .controller import LoopController

        report = LoopController(paths, _config(paths)).analyze()
    click.echo(json.dumps({k: report[k] for k in ("acceptance", "stopping", "catalog_version")}, indent=2))


@loop.group()
def experiments():
    """Create, validate and publish guided experiment definitions."""


@experiments.command("validate")
@click.argument("definition", type=click.Path(exists=True, dir_okay=False, path_type=Path))
def validate_cmd(definition: Path):
    parsed = ExperimentDefinition.model_validate_json(definition.read_text())
    click.echo(f"valid: {parsed.experiment_id} v{parsed.version}, {parsed.total_seconds:g} s")


@experiments.command("add")
@click.argument("definition", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.option("--publish", is_flag=True, help="Publish this version immediately.")
def add_cmd(definition: Path, publish: bool):
    paths = _paths()
    registry = ExperimentRegistry(paths.experiments / "definitions")
    parsed = registry.add(json.loads(definition.read_text()))
    if publish:
        registry.publish(parsed.experiment_id, parsed.version, by="cli")
    click.echo(f"stored {parsed.experiment_id} v{parsed.version}"
               + (" and published; the catalog updates on the next analysis" if publish else ""))


@experiments.command("retire")
@click.argument("experiment_id")
@click.option("--reason", required=True)
def retire_cmd(experiment_id: str, reason: str):
    paths = _paths()
    ExperimentRegistry(paths.experiments / "definitions").retire(experiment_id, by="cli", reason=reason)
    click.echo(f"retired {experiment_id}")


@experiments.command("export-seed-catalog")
@click.argument("out", type=click.Path(dir_okay=False, path_type=Path))
def export_seed_catalog(out: Path):
    """Write the offline catalog bundled into Collect (must match the seeds)."""
    definitions = [ExperimentDefinition.model_validate_json(p.read_text())
                   for p in sorted(SEED_DIR.glob("*.json"))]
    catalog = build_catalog(definitions, {}, catalog_version=0, generated_at="seed")
    out.write_text(json.dumps(catalog, indent=2, sort_keys=True) + "\n")
    click.echo(f"wrote {len(definitions)} seed experiments -> {out}")


@loop.command("install-agent")
@click.option("--load", is_flag=True, help="Also load it now with launchctl.")
def install_agent(load: bool):
    """Start the service automatically at login (user LaunchAgent)."""
    paths = _paths()
    executable = Path(sys.executable).with_name("chewsense-vision")
    plist = Path.home() / "Library/LaunchAgents" / f"{AGENT_LABEL}.plist"
    logs = Path.home() / "Library/Logs/ChewSense"
    logs.mkdir(parents=True, exist_ok=True)
    env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin:/opt/homebrew/bin")}
    if os.environ.get("CHEWSENSE_WORKSPACE"):
        env["CHEWSENSE_WORKSPACE"] = os.environ["CHEWSENSE_WORKSPACE"]
    if os.environ.get("CHEWSENSE_MODELS_DIR"):
        env["CHEWSENSE_MODELS_DIR"] = os.environ["CHEWSENSE_MODELS_DIR"]
    plist.parent.mkdir(parents=True, exist_ok=True)
    plist.write_bytes(plistlib.dumps({
        "Label": AGENT_LABEL,
        "ProgramArguments": [str(executable), "loop", "serve"],
        "RunAtLoad": True,
        "KeepAlive": {"SuccessfulExit": False},
        "ThrottleInterval": 30,
        "WorkingDirectory": str(paths.root),
        "EnvironmentVariables": env,
        "StandardOutPath": str(logs / "loop.log"),
        "StandardErrorPath": str(logs / "loop.log"),
        "ProcessType": "Background",
    }))
    click.echo(f"wrote {plist}")
    if load:
        os.system(f"launchctl bootstrap gui/{os.getuid()} '{plist}'")
    else:
        click.echo(f"Load it with: launchctl bootstrap gui/$(id -u) '{plist}'")
