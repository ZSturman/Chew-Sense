"""Local service connecting Collect (iPhone) and Review (Mac).

* Device API — HTTPS on the LAN, bearer token from explicit pairing.
* Admin API  — plain HTTP bound to 127.0.0.1 only, authenticated with the
  token in ``loop/service/admin.token`` (mode 0600), used by Review.

Uploads are resumable: the client asks for the current offset and appends
chunks; completion verifies size and SHA-256 before anything is imported.
Nothing here listens on a public interface by default beyond the LAN port,
and no endpoint works without a token except ``/v1/hello`` and ``/v1/pair``.
"""
from __future__ import annotations

import json
import re
import socket
import threading
import uuid
from concurrent.futures import Future
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from ..canonical import utc_now
from .controller import LoopController
from .experiments import ExperimentRegistry
from .guided import write_json_atomic
from .importer import sha256_file
from .lmstudio import LMStudio
from .pairing import DeviceRegistry, ServiceIdentity
from .processing import Processor
from .project_status import compute_project_status, project_status_path, write_project_status
from .review_tasks import review_state
from .requirements import build_requirements
from .store import LoopPaths, read_json
from .training import launch

API_VERSION = 1
_SESSION = re.compile(r"^[A-Za-z0-9._-]{1,128}$")
_HEX64 = re.compile(r"^[0-9a-f]{64}$")


class LoopService:
    """Owns the processor, controller and device registry for both servers."""

    def __init__(self, paths: LoopPaths, config: dict[str, Any], *, vision_runner=None,
                 trainer=None, lm: LMStudio | None = None, proposer=None):
        self.paths = paths.ensure()
        self.config = config
        self.identity = ServiceIdentity(paths.service).ensure()
        service = config["service"]
        self.devices = DeviceRegistry(paths.service / "devices.json", self.identity.fingerprint,
                                      window_s=service["pairing_window_s"],
                                      attempts=service["pairing_attempts"])
        registry = ExperimentRegistry(paths.experiments / "definitions")
        registry.seed()
        if lm is None and config["lmstudio"]["enabled"]:
            lm = LMStudio(config["lmstudio"]["base_url"], timeout_s=config["lmstudio"]["timeout_s"],
                          model=config["lmstudio"]["model"])
        self._analysis_lock = threading.Lock()
        self._analysis_requested = threading.Event()
        self._summarizing = threading.Event()
        self.training_stop = threading.Event()
        for request in (paths.root / "training").glob("run-*/request.json"):
            state = request.parent / "state.json"
            if read_json(state, {}).get("state", "requested") in {"requested", "running"}:
                write_json_atomic(state, {"state": "failed", "error": "Service restarted before training completed; outputs preserved."})
        self.controller = LoopController(paths, config, lm=lm,
                                         trainer=trainer if trainer is not None else self.schedule_training)
        self.processor = Processor(paths, config, vision_runner=vision_runner, proposer=proposer,
                                   on_session_done=lambda _sid: self._analysis_requested.set())
        if not paths.catalog.exists():
            self.analyze()

    def schedule_training(self, request_path: Path) -> Future:
        future = Future()

        def run():
            if not future.set_running_or_notify_cancel():
                return
            try:
                result = launch(request_path, stop_event=self.training_stop)
            except Exception as exc:  # noqa: BLE001
                future.set_exception(exc)
            else:
                future.set_result(result)
            finally:
                self._analysis_requested.set()

        threading.Thread(target=run, daemon=True, name="chewsense-training").start()
        return future

    def analyze(self) -> dict[str, Any]:
        with self._analysis_lock:
            self._analysis_requested.clear()
            report = self.controller.analyze()
        if report.get("lmstudio") is None and not self._summarizing.is_set():
            self._summarizing.set()

            def run():
                try:
                    if self.controller.summarize(report):
                        with self._analysis_lock:
                            self.controller.analyze()  # attach the cached summary
                finally:
                    self._summarizing.clear()

            threading.Thread(target=run, daemon=True).start()
        return report

    def periodic(self) -> None:
        previous_tasks = (read_json(self.paths.review_tasks, {}) or {}).get("tasks", [])
        if self.processor.finalize_verified_drafts():
            self._analysis_requested.set()
        tasks = self.processor.refresh_review_tasks()
        if tasks != previous_tasks:
            # Timing-only confirmation is a saved draft, not an event review
            # to freeze. Its completed task must still refresh phone status.
            self._analysis_requested.set()
        if self._analysis_requested.is_set():
            self.analyze()
        write_project_status(self.paths, compute_project_status(
            self.paths, self.config, records=self.controller.records()))

    # ---------------------------------------------------------- uploads
    def _upload_dir(self, upload_id: str) -> Path:
        if not re.fullmatch(r"[0-9a-f]{32}", upload_id):
            raise KeyError(upload_id)
        return self.paths.uploads / upload_id

    def create_upload(self, device_id: str, body: dict[str, Any]) -> dict[str, Any]:
        session_id, digest, size = body.get("session_id"), body.get("archive_sha256"), body.get("size_bytes")
        if not (isinstance(session_id, str) and _SESSION.match(session_id)):
            raise ValueError("invalid session_id")
        if not (isinstance(digest, str) and _HEX64.match(digest)):
            raise ValueError("archive_sha256 must be 64 lowercase hex characters")
        if not isinstance(size, int) or not 0 < size <= 4 * 1024**3:
            raise ValueError("size_bytes out of range")
        for job in self.processor.jobs():
            if job["archive_sha256"] == digest and job["state"] != "rejected":
                return {"upload_id": job.get("upload_id"), "offset": size, "state": "received",
                        "session_id": session_id}
        for folder in self.paths.uploads.glob("*/meta.json"):
            meta = read_json(folder)
            if meta and meta["archive_sha256"] == digest and meta["state"] == "receiving":
                part = folder.parent / "data.part"
                return {"upload_id": meta["upload_id"], "offset": part.stat().st_size if part.exists() else 0,
                        "state": "receiving", "session_id": session_id}
        upload_id = uuid.uuid4().hex
        folder = self.paths.uploads / upload_id
        folder.mkdir(parents=True)
        (folder / "data.part").touch()
        write_json_atomic(folder / "meta.json", {
            "upload_id": upload_id, "session_id": session_id, "archive_sha256": digest,
            "size_bytes": size, "device_id": device_id, "state": "receiving", "created_at": utc_now()})
        return {"upload_id": upload_id, "offset": 0, "state": "receiving", "session_id": session_id}

    def append_chunk(self, upload_id: str, offset: int, data: bytes) -> dict[str, Any]:
        folder = self._upload_dir(upload_id)
        meta = read_json(folder / "meta.json")
        if not meta or meta["state"] != "receiving":
            raise KeyError(upload_id)
        part = folder / "data.part"
        current = part.stat().st_size
        if offset != current:
            return {"offset": current, "conflict": True}
        if current + len(data) > meta["size_bytes"]:
            raise ValueError("chunk exceeds the announced size")
        with open(part, "ab") as handle:
            handle.write(data)
            handle.flush()
        return {"offset": current + len(data)}

    def complete_upload(self, upload_id: str) -> dict[str, Any]:
        folder = self._upload_dir(upload_id)
        meta = read_json(folder / "meta.json")
        if not meta:
            raise KeyError(upload_id)
        part, final = folder / "data.part", folder / "data.zip"
        if meta["state"] == "receiving":
            if part.stat().st_size != meta["size_bytes"]:
                return {"state": "incomplete", "offset": part.stat().st_size}
            if sha256_file(part) != meta["archive_sha256"]:
                part.unlink()
                part.touch()
                return {"state": "checksum_mismatch", "offset": 0}
            part.rename(final)
            meta.update(state="received", received_at=utc_now())
            write_json_atomic(folder / "meta.json", meta)
        job = self.processor.enqueue_archive(final, archive_sha256=meta["archive_sha256"],
                                             device=meta["device_id"], upload_id=upload_id)
        return {"state": "received", "session_id": meta["session_id"],
                "archive_sha256": meta["archive_sha256"], "received_at": meta.get("received_at"),
                "job_id": job["job_id"]}

    def session_status(self, session_id: str) -> dict[str, Any]:
        record = read_json(self.paths.session_record(session_id), {}) or {}
        job = next((j for j in reversed(self.processor.jobs()) if j.get("session_id") == session_id), None)
        state = "unknown"
        if job:
            state = {"queued": "queued", "running": "processing", "completed": "processed",
                     "needs_attention": "needs_attention", "rejected": "rejected"}[job["state"]]
        reviewed = review_state(self.paths.assisted, session_id) if record else "absent"
        return {"session_id": session_id, "state": state, "import_state": job and job.get("import_state"),
                "error": job and job.get("error"), "review": reviewed,
                "attention": record.get("attention")}

    def phone_status(self) -> dict[str, Any]:
        status = read_json(self.paths.status, {}) or {}
        catalog = read_json(self.paths.catalog, {}) or {}
        report = read_json(self.paths.reports / "loop/latest.json", {}) or {}
        readiness = report.get("training", {}).get("readiness", {})
        tasks = (read_json(self.paths.review_tasks, {}) or {}).get("tasks", [])
        requirements = report.get("requirements", {})
        if report.get("dataset") and readiness:
            # Task progress changes independently of a full model analysis.
            # Refresh the action list without rerunning inference/evaluation.
            requirements = build_requirements(
                self.controller.records(), report["dataset"], report["evaluation"],
                report["synchronization"], readiness, report["training"]["history"], tasks, self.config,
                eating=report.get("eating_detector"))
        actions = requirements.get("actions", [])
        project = compute_project_status(self.paths, self.config, records=self.controller.records(),
                                         service_status=status)
        # Send the stored copy so "updated" means "last changed" in every app, as Review shows it.
        write_project_status(self.paths, project)
        project = read_json(project_status_path(self.paths), project) or project
        first = next((s for s in project["steps"] if not s.get("optional")), None)
        # Send only wearer-facing status, never local paths, tokens or raw reviews.
        pipeline = {
            "review_tasks": len(tasks),
            "attention_count": len(status.get("attention", [])),
            "training_ready": readiness.get("ready", False),
            "research_targets_met": report.get("acceptance", {}).get("all_met", False),
            "motion_assessment_passed": report.get("stopping", {}).get("motion_assessment_passed", False),
            "blockers": [g["detail"] for g in readiness.get("gates", []) if not g["passed"]],
            "goals": requirements.get("goals", []),
            "stages": requirements.get("stages", []),
            "actions": actions,
            "received_sessions": requirements.get("received_sessions", 0),
            "complete_sessions": requirements.get("complete_sessions", 0),
            "incomplete_sessions": requirements.get("incomplete_sessions", 0),
            "available_review_tasks": requirements.get("review_tasks", 0),
            # The first Collect step, so the Start button matches the shared next steps.
            "next_experiment_id": next((s["experiment_id"] for s in project["steps"] if s.get("experiment_id")), None),
            # Older Collect builds show only this line; keep it identical to the shared message.
            "next_action": (f"{project['headline']}: {first['title']}. {first['detail']}" if first
                            else f"{project['headline']}. {project['summary']}"),
            "project": project,
        }
        return {"service": "chewsense-loop", "api_version": API_VERSION, "state": status.get("state"),
                "pending": len(status.get("pending", [])), "catalog_version": catalog.get("catalog_version"),
                "pipeline": pipeline}


def _make_handler(service: LoopService, *, admin: bool):
    max_chunk = service.config["service"]["max_chunk_bytes"]

    class Handler(BaseHTTPRequestHandler):
        server_version = "ChewSenseLoop/1"
        protocol_version = "HTTP/1.1"

        def log_message(self, fmt, *args):  # Keep logs small and free of tokens.
            pass

        def _send(self, code: int, payload: Any) -> None:
            body = json.dumps(payload, sort_keys=True, default=str).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def _body(self, limit: int = 1 << 20) -> bytes:
            length = int(self.headers.get("Content-Length") or 0)
            if length > limit:
                raise ValueError("request body too large")
            return self.rfile.read(length) if length else b""

        def _json(self) -> dict[str, Any]:
            raw = self._body()
            value = json.loads(raw) if raw else {}
            if not isinstance(value, dict):
                raise ValueError("expected a JSON object")
            return value

        def _token(self) -> str | None:
            header = self.headers.get("Authorization", "")
            return header[7:] if header.startswith("Bearer ") else None

        def _dispatch(self, method: str) -> None:
            url = urlparse(self.path)
            parts = [p for p in url.path.split("/") if p]
            try:
                if admin:
                    if not self._token() or self._token() != service.identity.admin_token:
                        return self._send(401, {"error": "admin token required"})
                    return self._admin(method, parts, url)
                return self._device(method, parts, url)
            except (ValueError, json.JSONDecodeError) as exc:
                return self._send(400, {"error": str(exc)})
            except KeyError:
                return self._send(404, {"error": "not found"})
            except PermissionError as exc:
                return self._send(403, {"error": str(exc)})

        def _device(self, method, parts, url):
            if method == "GET" and parts == ["v1", "hello"]:
                return self._send(200, {"service": "chewsense-loop", "api_version": API_VERSION,
                                        "name": socket.gethostname(),
                                        "pairing_open": service.devices.pairing_open})
            if method == "POST" and parts == ["v1", "pair"]:
                body = self._json()
                device_id, name, proof = body.get("device_id"), body.get("device_name", "iPhone"), body.get("proof")
                if not (isinstance(device_id, str) and _SESSION.match(device_id) and isinstance(proof, str)):
                    raise ValueError("device_id and proof are required")
                return self._send(200, service.devices.pair(device_id, str(name), proof))
            auth = service.devices.authenticate(self._token())
            if auth is None:
                return self._send(401, {"error": "pair this iPhone with the Mac first"})
            device_id, _ = auth
            if method == "GET" and parts == ["v1", "status"]:
                return self._send(200, service.phone_status())
            if method == "GET" and parts == ["v1", "catalog"]:
                catalog = read_json(service.paths.catalog)
                if catalog is None:
                    raise KeyError("catalog")
                known = parse_qs(url.query).get("since", [None])[0]
                if known is not None and str(catalog["catalog_version"]) == known:
                    return self._send(200, {"unchanged": True, "catalog_version": catalog["catalog_version"]})
                return self._send(200, catalog)
            if method == "POST" and parts == ["v1", "uploads"]:
                return self._send(200, service.create_upload(device_id, self._json()))
            if len(parts) == 3 and parts[:2] == ["v1", "uploads"] and method == "PUT":
                offset = int(parse_qs(url.query).get("offset", ["-1"])[0])
                data = self._body(limit=max_chunk)
                result = service.append_chunk(parts[2], offset, data)
                return self._send(409 if result.get("conflict") else 200, result)
            if len(parts) == 4 and parts[:2] == ["v1", "uploads"] and parts[3] == "complete" and method == "POST":
                return self._send(200, service.complete_upload(parts[2]))
            if len(parts) == 4 and parts[:2] == ["v1", "sessions"] and parts[3] == "status" and method == "GET":
                if not _SESSION.match(parts[2]):
                    raise ValueError("invalid session id")
                return self._send(200, service.session_status(parts[2]))
            raise KeyError(self.path)

        def _admin(self, method, parts, url):
            processor = service.processor
            routes_get = {
                ("admin", "status"): lambda: read_json(service.paths.status, {}),
                ("admin", "dashboard"): lambda: read_json(service.paths.reports / "loop/latest.json", {}),
                ("admin", "review-tasks"): lambda: read_json(service.paths.review_tasks, {"tasks": []}),
                ("admin", "catalog"): lambda: read_json(service.paths.catalog, {}),
                ("admin", "devices"): lambda: {"devices": service.devices.list(),
                                               "fingerprint": service.identity.fingerprint,
                                               "pairing_open": service.devices.pairing_open},
            }
            def control(action):
                action()
                return processor.write_status()

            routes_post = {
                ("admin", "pause"): lambda: control(processor.pause),
                ("admin", "resume"): lambda: control(processor.resume),
                ("admin", "stop"): lambda: control(processor.stop),
                ("admin", "pairing"): service.devices.open_window,
                ("admin", "analyze"): service.analyze,
            }
            key = tuple(parts)
            if method == "GET" and key in routes_get:
                return self._send(200, routes_get[key]())
            if method == "POST" and key in routes_post:
                return self._send(200, routes_post[key]())
            if method == "POST" and len(parts) == 4 and parts[:2] == ["admin", "jobs"] and parts[3] == "retry":
                return self._send(200, processor.retry(parts[2]))
            if method == "POST" and len(parts) == 4 and parts[:2] == ["admin", "devices"] and parts[3] == "revoke":
                service.devices.revoke(parts[2])
                return self._send(200, {"revoked": parts[2]})
            if method == "POST" and len(parts) == 4 and parts[:2] == ["admin", "experiments"]:
                registry = service.controller.registry
                body = self._json()
                if parts[3] == "retire":
                    registry.retire(parts[2], by="review", reason=str(body.get("reason", "retired")))
                elif parts[3] == "publish":
                    registry.publish(parts[2], int(body["version"]), by="review")
                else:
                    raise KeyError(parts[3])
                return self._send(200, {"ok": True, "catalog_version": service.analyze()["catalog_version"]})
            raise KeyError(self.path)

        def do_GET(self):
            self._dispatch("GET")

        def do_POST(self):
            self._dispatch("POST")

        def do_PUT(self):
            self._dispatch("PUT")

    return Handler


class _Server(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True


def serve(service: LoopService, *, host: str = "0.0.0.0", device_port: int | None = None,
          admin_port: int | None = None, advertise: bool = True) -> tuple[_Server, _Server, threading.Event]:
    """Start both servers and the worker; returns handles for shutdown."""
    device_port = service.config["service"]["device_port"] if device_port is None else device_port
    admin_port = service.config["service"]["admin_port"] if admin_port is None else admin_port
    device = _Server((host, device_port), _make_handler(service, admin=False))
    device.socket = service.identity.ssl_context().wrap_socket(device.socket, server_side=True)
    admin = _Server(("127.0.0.1", admin_port), _make_handler(service, admin=True))
    stop = threading.Event()
    for target in (device.serve_forever, admin.serve_forever):
        threading.Thread(target=target, daemon=True).start()
    threading.Thread(target=service.processor.run_forever, args=(stop, service.periodic), daemon=True).start()
    write_json_atomic(service.paths.service / "endpoint.json", {
        "device_port": device.server_address[1], "admin_port": admin.server_address[1],
        "fingerprint": service.identity.fingerprint, "started_at": utc_now()})
    if advertise:
        _advertise(device.server_address[1])
    service.processor.write_status()
    return device, admin, stop


_dns_sd = None


def _advertise(port: int) -> None:
    """Bonjour advertisement through the system dns-sd tool (macOS)."""
    global _dns_sd
    import shutil
    import subprocess

    tool = shutil.which("dns-sd")
    if tool and _dns_sd is None:
        _dns_sd = subprocess.Popen([tool, "-R", f"ChewSense on {socket.gethostname()}",
                                    "_chewsense._tcp", "local", str(port), "api=1"],
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
