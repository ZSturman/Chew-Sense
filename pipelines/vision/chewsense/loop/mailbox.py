"""The home-server mailbox: recordings the iPhone left while this Mac was away.

Collect sends a recording straight to this Mac whenever the Mac answers. Only
when it doesn't does Collect deposit it in Home Services on the Pi
(``/api/v1/chewsense``, see Home Services ``docs/CHEWSENSE_MAILBOX.md``),
encrypted on the phone with the key in ``loop/service/mailbox.key``. While the
Mac is awake, the collector here downloads waiting deposits (resumable),
verifies the sealed hash, decrypts, verifies the archive hash and hands the
archive to the same ``enqueue_archive`` path a direct upload uses. It
acknowledges only once the import result is known, and only then does the Pi
delete its copy. It also publishes a status copy so phones can show where
things stand while the Mac sleeps.

Configuration (``loop/service/mailbox.json``, set with ``loop mailbox-configure``)::

    {"url": "https://kiosk-lab.<tailnet>.ts.net:8443",
     "mac_token_file": "mailbox-mac.token", "phone_token_file": "mailbox-phone.token"}

Optional ``cafile``/``fingerprint`` exist for isolated tests with a
self-signed server; the real Pi presents a public certificate.

Sealed format (``CSMB`` v1), written by Collect (``LoopSeal`` in
``apps/shared-capture/LoopMailbox.swift``) and by :func:`seal` in tests::

    header  = b"CSMB" + b"\\x01" + 8-byte random nonce prefix
    record  = 4-byte big-endian length + AES-256-GCM ciphertext and tag
    nonce_i = prefix + i (4 bytes big-endian)
    aad_i   = header + i (4 bytes) + final flag (1 byte, 1 on the last record)

Plaintext records are at most 4 MiB. The final flag makes truncation fail.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import secrets
import ssl
import struct
import threading
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Callable

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from ..canonical import utc_now
from .guided import write_json_atomic
from .importer import sha256_file
from .store import LoopPaths, read_json

MAGIC = b"CSMB\x01"
HEADER = len(MAGIC) + 8
CHUNK = 4 * 1024 * 1024
TAG = 16
_IMPORTED = {"imported", "duplicate"}


class MailboxError(RuntimeError):
    """The Pi or a deposit could not be used this cycle; nothing was lost."""


# --------------------------------------------------------------- sealed files
def seal(source: Path, target: Path, key: bytes) -> None:
    prefix = secrets.token_bytes(8)
    header = MAGIC + prefix
    cipher = AESGCM(key)
    with open(source, "rb") as src, open(target, "wb") as out:
        out.write(header)
        index, chunk = 0, src.read(CHUNK)
        while True:
            following = src.read(CHUNK) if chunk else b""
            final = not following
            aad = header + struct.pack(">IB", index, int(final))
            sealed = cipher.encrypt(prefix + struct.pack(">I", index), chunk, aad)
            out.write(struct.pack(">I", len(sealed)) + sealed)
            if final:
                return
            index, chunk = index + 1, following


def unseal(source: Path, target: Path, key: bytes) -> None:
    """Decrypt a sealed archive; raises MailboxError on any tampering or truncation."""
    cipher = AESGCM(key)
    size = source.stat().st_size
    with open(source, "rb") as src, open(target, "wb") as out:
        header = src.read(HEADER)
        if len(header) != HEADER or not header.startswith(MAGIC):
            raise MailboxError("not a sealed Chew Sense archive")
        prefix, index = header[len(MAGIC):], 0
        while True:
            raw = src.read(4)
            if len(raw) != 4:
                raise MailboxError("sealed archive is truncated")
            (length,) = struct.unpack(">I", raw)
            if not TAG <= length <= CHUNK + TAG:
                raise MailboxError("sealed archive has an invalid record")
            sealed = src.read(length)
            if len(sealed) != length:
                raise MailboxError("sealed archive is truncated")
            final = src.tell() == size
            aad = header + struct.pack(">IB", index, int(final))
            try:
                out.write(cipher.decrypt(prefix + struct.pack(">I", index), sealed, aad))
            except Exception as exc:  # cryptography raises InvalidTag
                raise MailboxError("sealed archive failed authentication (wrong key, damaged or cut short)") from exc
            if final:
                return
            index += 1


# --------------------------------------------------------------- configuration
class MailboxConfig:
    def __init__(self, service_dir: Path):
        self.dir = Path(service_dir)
        self.path = self.dir / "mailbox.json"
        raw = read_json(self.path, None)
        self.enabled = bool(raw and raw.get("url"))
        raw = raw or {}
        self.url = str(raw.get("url", "")).rstrip("/")
        self.interval_s = float(raw.get("interval_s", 30))
        self.cafile = self._resolve(raw.get("cafile"))
        self.fingerprint = raw.get("fingerprint")
        self._mac_token = self._resolve(raw.get("mac_token_file", "mailbox-mac.token"))
        self._phone_token = self._resolve(raw.get("phone_token_file", "mailbox-phone.token"))

    def _resolve(self, name: str | None) -> Path | None:
        if not name:
            return None
        path = Path(name)
        return path if path.is_absolute() else self.dir / path

    @staticmethod
    def _secret(path: Path | None) -> str | None:
        try:
            return path.read_text().strip() or None if path else None
        except FileNotFoundError:
            return None

    @property
    def mac_token(self) -> str | None:
        return self._secret(self._mac_token)

    @property
    def phone_token(self) -> str | None:
        return self._secret(self._phone_token)

    def key(self) -> bytes:
        """The deposit key, created once; only this Mac and its paired phones hold it."""
        path = self.dir / "mailbox.key"
        if not path.exists():
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "wb") as handle:
                handle.write(secrets.token_bytes(32))
        key = path.read_bytes()
        if len(key) != 32:
            raise MailboxError("mailbox.key must hold 32 bytes")
        return key

    def phone_offer(self) -> dict[str, Any] | None:
        """What paired phones need, sent only inside the pinned, authenticated /v1/status."""
        if not (self.enabled and self.phone_token and self.mac_token):
            return None
        key = self.key()
        offer = {"url": self.url, "token": self.phone_token, "key": base64.b64encode(key).decode(),
                 "key_id": hashlib.sha256(key).hexdigest()[:16]}
        if self.fingerprint:
            offer["fingerprint"] = self.fingerprint
        return offer

    @staticmethod
    def write(service_dir: Path, url: str, mac_token: str, phone_token: str) -> Path:
        service_dir = Path(service_dir)
        for name, token in (("mailbox-mac.token", mac_token), ("mailbox-phone.token", phone_token)):
            path = service_dir / name
            path.unlink(missing_ok=True)
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "w") as handle:
                handle.write(token.strip())
        write_json_atomic(service_dir / "mailbox.json", {"url": url.rstrip("/")})
        return service_dir / "mailbox.json"


# --------------------------------------------------------------- collector
class MailboxCollector:
    """Runs while the Mac is awake; every cycle is safe to interrupt and repeat."""

    def __init__(self, paths: LoopPaths, config: MailboxConfig, *, enqueue: Callable[..., dict],
                 jobs: Callable[[], list[dict]], status: Callable[[], dict], session_state: Callable[[str], str]):
        self.paths, self.config = paths, config
        self.enqueue, self.jobs, self.status, self.session_state = enqueue, jobs, status, session_state
        self.awake = threading.Event()
        self.awake.set()
        context = ssl.create_default_context(cafile=str(config.cafile) if config.cafile else None)
        self._context = context
        self.state_path = paths.root / "state" / "mailbox.json"

    # transport ---------------------------------------------------------------
    def _request(self, method: str, path: str, body: Any = None, headers: dict | None = None,
                 timeout: float = 30) -> urllib.request.addinfourl:
        token = self.config.mac_token
        if not token:
            raise MailboxError("no Mac token for the home-server mailbox")
        data = json.dumps(body).encode() if body is not None else None
        request = urllib.request.Request(f"{self.config.url}/api/v1/chewsense/{path}", data=data, method=method,
                                         headers={"Authorization": f"Bearer {token}", "Home-Services-Contract": "1",
                                                  "Content-Type": "application/json", **(headers or {})})
        try:
            return urllib.request.urlopen(request, timeout=timeout, context=self._context)
        except urllib.error.HTTPError as exc:
            try:
                message = json.loads(exc.read())["error"]["message"]
            except Exception:  # noqa: BLE001
                message = exc.reason
            raise MailboxError(f"home server {exc.code}: {message}") from exc
        except (urllib.error.URLError, OSError) as exc:
            raise MailboxError(f"home server unreachable: {exc}") from exc

    def _json(self, method: str, path: str, body: Any = None) -> Any:
        with self._request(method, path, body) as response:
            return json.loads(response.read())

    # cycle -------------------------------------------------------------------
    def run_forever(self, stop: threading.Event) -> None:
        while not stop.is_set():
            if self.awake.is_set():
                self.cycle()
            stop.wait(self.config.interval_s)

    def cycle(self) -> dict[str, Any]:
        record = read_json(self.state_path, {}) or {}
        record.setdefault("deposits", {})
        try:
            self.publish_status(record)
            for deposit in self._json("GET", "pending").get("deposits", []):
                self.collect(deposit, record)
            record.update(last_cycle_at=utc_now(), last_error=None)
        except MailboxError as exc:
            record.update(last_cycle_at=utc_now(), last_error=str(exc))
        write_json_atomic(self.state_path, record)
        return record

    def publish_status(self, record: dict) -> None:
        recent = list(record["deposits"].values())[-200:]
        sessions = {d["session_id"]: self.session_state(d["session_id"]) for d in recent if d.get("session_id")}
        self._json("PUT", "status", {"status": self.status(), "sessions": sessions, "published_at": utc_now()})

    def collect(self, deposit: dict, record: dict) -> None:
        entry = record["deposits"].setdefault(deposit["id"], {"session_id": deposit["session_id"]})
        if "job_id" not in entry:
            # Always verify the deposit itself, even when its announced hash is
            # already known: an announcement is a claim, the decrypted bytes are proof.
            try:
                archive = self._download(deposit)
            except MailboxError as exc:
                if "authentication" in str(exc) or "archive hash" in str(exc):
                    self._ack(deposit, "rejected", str(exc), entry)
                    return
                raise
            # The same archive never creates a second job (enqueue_job deduplicates).
            job = self.enqueue(archive, archive_sha256=deposit["archive_sha256"],
                               device=f"{deposit['device']} (home server)")
            if job.get("archive") != str(archive):
                archive.unlink(missing_ok=True)  # an existing job already holds this archive
            entry["job_id"] = job["job_id"]
        job = next((j for j in self.jobs() if j["job_id"] == entry["job_id"]), None)
        if job is None:
            return
        if job.get("import_state") in _IMPORTED:
            self._ack(deposit, job["import_state"], None, entry)
        elif job.get("state") == "rejected":
            self._ack(deposit, "rejected", job.get("error"), entry)
        # Otherwise the import has not run yet; the Pi keeps its copy until it has.

    def _ack(self, deposit: dict, result: str, detail: str | None, entry: dict) -> None:
        self._json("POST", f"deposits/{deposit['id']}/ack",
                   {"result": result, "session_id": deposit["session_id"], "detail": detail})
        entry.update(acknowledged=result, acknowledged_at=utc_now())

    def _download(self, deposit: dict) -> Path:
        folder = self.paths.uploads / f"mailbox-{deposit['id']}"
        folder.mkdir(parents=True, exist_ok=True)
        sealed, archive = folder / "sealed.part", folder / "data.zip"
        have = sealed.stat().st_size if sealed.exists() else 0
        if have > deposit["size"]:
            sealed.unlink()
            have = 0
        if have < deposit["size"]:
            headers = {"Range": f"bytes={have}-"} if have else {}
            with self._request("GET", f"deposits/{deposit['id']}/content", headers=headers, timeout=60) as response:
                if have and response.status != 206:
                    sealed.unlink()  # the server ignored the range; start over next cycle
                    raise MailboxError("home server ignored the resume request")
                with open(sealed, "ab") as out:
                    for block in iter(lambda: response.read(1 << 20), b""):
                        out.write(block)
        if sealed.stat().st_size != deposit["size"] or sha256_file(sealed) != deposit["sha256"]:
            sealed.unlink()
            raise MailboxError("downloaded deposit did not match its hash; it will be fetched again")
        unseal(sealed, archive, self.config.key())
        if sha256_file(archive) != deposit["archive_sha256"]:
            archive.unlink()
            raise MailboxError("decrypted archive hash does not match what the phone announced")
        sealed.unlink()
        return archive

