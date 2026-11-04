"""Service identity, explicit pairing and device tokens.

Transport is TLS with a self-signed certificate created on first start. The
iPhone pins its SHA-256 fingerprint. Pairing binds that fingerprint to a
short-lived six-digit code shown on the Mac:

    proof = HMAC-SHA256(code, "pair|<device_id>|<fingerprint seen by phone>")

A relay presenting a different certificate cannot produce the proof the Mac
expects without the code, and attempts are limited per pairing window. Only
SHA-256 digests of device tokens are stored.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import ssl
import subprocess
import threading
import time
from pathlib import Path
from typing import Any

from ..canonical import utc_now
from .guided import write_json_atomic


def _digest(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def pairing_proof(code: str, device_id: str, fingerprint: str) -> str:
    return hmac.new(code.encode(), f"pair|{device_id}|{fingerprint}".encode(), hashlib.sha256).hexdigest()


def server_proof(code: str, device_id: str, fingerprint: str, token: str) -> str:
    message = f"server|{device_id}|{fingerprint}|{token}".encode()
    return hmac.new(code.encode(), message, hashlib.sha256).hexdigest()


class ServiceIdentity:
    def __init__(self, service_dir: Path):
        self.dir = Path(service_dir)
        self.cert = self.dir / "tls-cert.pem"
        self.key = self.dir / "tls-key.pem"
        self.admin_token_path = self.dir / "admin.token"

    def ensure(self, openssl: str = "openssl") -> "ServiceIdentity":
        self.dir.mkdir(parents=True, exist_ok=True)
        os.chmod(self.dir, 0o700)
        if not (self.cert.exists() and self.key.exists()):
            subprocess.run(
                [openssl, "req", "-x509", "-newkey", "ec", "-pkeyopt",
                 "ec_paramgen_curve:prime256v1", "-nodes", "-days", "3650",
                 "-subj", "/CN=ChewSense Loop", "-keyout", str(self.key), "-out", str(self.cert)],
                check=True, capture_output=True)
            os.chmod(self.key, 0o600)
        if not self.admin_token_path.exists():
            fd = os.open(self.admin_token_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "w") as handle:
                handle.write(secrets.token_urlsafe(32))
        return self

    @property
    def fingerprint(self) -> str:
        der = ssl.PEM_cert_to_DER_cert(self.cert.read_text())
        return hashlib.sha256(der).hexdigest()

    @property
    def admin_token(self) -> str:
        return self.admin_token_path.read_text().strip()

    def ssl_context(self) -> ssl.SSLContext:
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.minimum_version = ssl.TLSVersion.TLSv1_2
        context.load_cert_chain(self.cert, self.key)
        return context


class DeviceRegistry:
    def __init__(self, path: Path, fingerprint: str, *, window_s: float = 600, attempts: int = 5):
        self.path = Path(path)
        self.fingerprint = fingerprint
        self.window_s = window_s
        self.max_attempts = attempts
        self._lock = threading.Lock()
        self._code: str | None = None
        self._expires = 0.0
        self._attempts = 0

    def _devices(self) -> dict[str, Any]:
        return json.loads(self.path.read_text()) if self.path.exists() else {}

    def _save(self, devices: dict[str, Any]) -> None:
        write_json_atomic(self.path, devices)
        os.chmod(self.path, 0o600)

    def open_window(self) -> dict[str, Any]:
        with self._lock:
            self._code = f"{secrets.randbelow(10**6):06d}"
            self._expires = time.monotonic() + self.window_s
            self._attempts = self.max_attempts
            return {"code": self._code, "expires_in_s": self.window_s,
                    "fingerprint": self.fingerprint}

    def close_window(self) -> None:
        with self._lock:
            self._code = None

    @property
    def pairing_open(self) -> bool:
        return self._code is not None and time.monotonic() < self._expires and self._attempts > 0

    def pair(self, device_id: str, device_name: str, proof: str) -> dict[str, Any]:
        with self._lock:
            if not self.pairing_open:
                raise PermissionError("pairing is not open on the Mac")
            self._attempts -= 1
            assert self._code is not None
            expected = pairing_proof(self._code, device_id, self.fingerprint)
            if not hmac.compare_digest(expected, proof):
                if self._attempts <= 0:
                    self._code = None
                raise PermissionError("pairing code or certificate did not match")
            code, self._code = self._code, None
            token = secrets.token_urlsafe(32)
            devices = self._devices()
            devices[device_id] = {"name": device_name[:80], "token_sha256": _digest(token),
                                  "paired_at": utc_now(), "last_seen": None, "revoked": False}
            self._save(devices)
            return {"device_token": token, "fingerprint": self.fingerprint,
                    "server_proof": server_proof(code, device_id, self.fingerprint, token)}

    def authenticate(self, token: str | None) -> tuple[str, dict[str, Any]] | None:
        if not token:
            return None
        digest = _digest(token)
        with self._lock:
            devices = self._devices()
            for device_id, device in devices.items():
                if not device.get("revoked") and hmac.compare_digest(device["token_sha256"], digest):
                    device["last_seen"] = utc_now()
                    self._save(devices)
                    return device_id, device
        return None

    def revoke(self, device_id: str) -> None:
        with self._lock:
            devices = self._devices()
            if device_id not in devices:
                raise KeyError(device_id)
            devices[device_id]["revoked"] = True
            self._save(devices)

    def list(self) -> list[dict[str, Any]]:
        return [{"device_id": k, **{f: v[f] for f in ("name", "paired_at", "last_seen", "revoked")}}
                for k, v in self._devices().items()]
