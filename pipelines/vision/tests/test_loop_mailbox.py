"""Home-server mailbox: sealed archives and the Mac collector (synthetic recordings only)."""
from __future__ import annotations

import hashlib
import io
import json
import secrets
import zipfile
from pathlib import Path

import pytest

from chewsense.loop import mailbox as mb
from chewsense.loop.store import LoopPaths

from .loop_fixtures import fake_vision, loop_config, make_session, make_video, zip_session


@pytest.fixture(scope="module")
def video(tmp_path_factory):
    return make_video(tmp_path_factory.mktemp("video") / "pattern.mov", seconds=4)


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_sealed_archive_round_trips_and_rejects_tampering(tmp_path, monkeypatch):
    monkeypatch.setattr(mb, "CHUNK", 1000)
    key = secrets.token_bytes(32)
    for size in (0, 999, 1000, 3500):
        plain = tmp_path / f"p{size}"
        plain.write_bytes(secrets.token_bytes(size))
        mb.seal(plain, tmp_path / "s", key)
        mb.unseal(tmp_path / "s", tmp_path / "o", key)
        assert (tmp_path / "o").read_bytes() == plain.read_bytes()
    sealed = (tmp_path / "s").read_bytes()
    for damaged in (sealed[:-1], sealed[:-(1016 + 4)], sealed[:20] + bytes([sealed[20] ^ 1]) + sealed[21:]):
        (tmp_path / "d").write_bytes(damaged)
        with pytest.raises(mb.MailboxError):
            mb.unseal(tmp_path / "d", tmp_path / "o", key)
    with pytest.raises(mb.MailboxError):
        mb.unseal(tmp_path / "s", tmp_path / "o", secrets.token_bytes(32))


class FakePi:
    """Just enough of /api/v1/chewsense for the collector."""

    def __init__(self):
        self.deposits, self.blobs, self.acks, self.status, self.ranges = {}, {}, [], None, []

    def add(self, deposit_id, session_id, sealed: Path, archive_sha: str):
        self.blobs[deposit_id] = sealed.read_bytes()
        self.deposits[deposit_id] = {"id": deposit_id, "session_id": session_id, "sha256": sha(sealed),
                                     "size": sealed.stat().st_size, "archive_sha256": archive_sha,
                                     "device": "Test iPhone", "state": "waiting"}

    def request(self, method, path, body=None, headers=None, timeout=30):
        class Response(io.BytesIO):
            status = 200

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

        if path == "pending":
            return Response(json.dumps({"deposits": [d for d in self.deposits.values()
                                                     if d["state"] == "waiting"]}).encode())
        if path == "status":
            self.status = body
            return Response(b"{}")
        deposit_id = path.split("/")[1]
        if path.endswith("/content"):
            data = self.blobs[deposit_id]
            start = int(headers["Range"][6:-1]) if headers and "Range" in headers else 0
            self.ranges.append(start)
            response = Response(data[start:])
            response.status = 206 if start else 200
            return response
        if path.endswith("/ack"):
            self.acks.append((deposit_id, body["result"]))
            self.deposits[deposit_id]["state"] = "collected"
            del self.blobs[deposit_id]
            return Response(b"{}")
        raise AssertionError(path)


@pytest.fixture
def mac(tmp_path):
    from chewsense.loop.server import LoopService

    paths = LoopPaths(tmp_path / "loop")
    paths.ensure()
    mb.MailboxConfig.write(paths.service, "https://pi.example.ts.net:8443", "mac-token", "phone-token")
    service = LoopService(paths, loop_config(), vision_runner=fake_vision(), trainer=lambda _p: None,
                          proposer=lambda *_a: {"summary": {}})
    pi = FakePi()
    service.mailbox._request = pi.request
    return service, pi, paths


def deposit(tmp_path, pi, service, deposit_id, session_id, video):
    folder = make_session(tmp_path / f"phone-{deposit_id}", session_id, video=video, video_seconds=4,
                          coarse=[dict(start_ts=0, end_ts=4, label="chewing", source="human_coarse")])
    archive = zip_session(folder, tmp_path / f"{deposit_id}.zip")
    sealed = tmp_path / f"{deposit_id}.sealed"
    mb.seal(archive, sealed, service.mailbox_config.key())
    pi.add(deposit_id, session_id, sealed, sha(archive))
    return folder


def test_mac_collects_imports_then_acknowledges_and_publishes_status(tmp_path, mac, video):
    service, pi, paths = mac
    deposit(tmp_path, pi, service, "dep_1", "MB-20990101-000001-AAAAAAAA", video)
    # A download cut short last time resumes from where it stopped.
    folder = paths.uploads / "mailbox-dep_1"
    folder.mkdir(parents=True)
    (folder / "sealed.part").write_bytes(pi.blobs["dep_1"][:500])

    service.mailbox.cycle()
    assert pi.ranges == [500]
    assert pi.acks == []  # not before the import has run: the Pi keeps its copy
    assert len(service.processor.jobs()) == 1
    while service.processor.run_once():
        pass
    record = service.mailbox.cycle()
    assert pi.acks == [("dep_1", "imported")] and record["last_error"] is None
    assert paths.source("MB-20990101-000001-AAAAAAAA").exists()
    assert pi.status["sessions"] == {"MB-20990101-000001-AAAAAAAA": "processed"}
    assert "routes" not in pi.status["status"] and "mailbox" not in pi.status["status"]
    assert not folder.exists()


def test_resent_recording_is_a_duplicate_and_bad_deposits_are_rejected(tmp_path, mac, video):
    service, pi, paths = mac
    folder = deposit(tmp_path, pi, service, "dep_1", "MB-20990101-000002-BBBBBBBB", video)
    service.mailbox.cycle()
    while service.processor.run_once():
        pass
    service.mailbox.cycle()
    # The phone lost its queue and deposited the same recording again; the new
    # ZIP differs byte for byte (stored, not deflated) but holds the same files.
    archive = tmp_path / "again.zip"
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_STORED) as out:
        for path in sorted(folder.rglob("*")):
            if path.is_file():
                out.write(path, f"{folder.name}/{path.relative_to(folder)}")
    sealed = tmp_path / "again.sealed"
    mb.seal(archive, sealed, service.mailbox_config.key())
    pi.add("dep_2", "MB-20990101-000002-BBBBBBBB", sealed, sha(archive))
    # And one sealed with a key this Mac never issued.
    mb.seal(archive, tmp_path / "foreign.sealed", secrets.token_bytes(32))
    pi.add("dep_3", "MB-20990101-000003-CCCCCCCC", tmp_path / "foreign.sealed", sha(archive))
    service.mailbox.cycle()
    while service.processor.run_once():
        pass
    service.mailbox.cycle()
    assert sorted(pi.acks) == [("dep_1", "imported"), ("dep_2", "duplicate"), ("dep_3", "rejected")]
    # The duplicate leaves the original provenance in place.
    record = json.loads(paths.session_record("MB-20990101-000002-BBBBBBBB").read_text())
    first = next(j for j in service.processor.jobs() if j.get("import_state") == "imported")
    assert record["archive_sha256"] == first["archive_sha256"] and record["processing"] == "completed"
    assert record["last_duplicate_from"].endswith("(home server)")
    assert [p.name for p in paths.sources.iterdir() if not p.name.startswith(".")] == ["MB-20990101-000002-BBBBBBBB"]
    assert not list(paths.quarantine.iterdir())


def test_phones_get_the_mailbox_only_inside_authenticated_status(tmp_path, mac):
    service, _pi, paths = mac
    offer = service.phone_status()["mailbox"]
    assert offer["url"] == "https://pi.example.ts.net:8443" and offer["token"] == "phone-token"
    assert len(__import__("base64").b64decode(offer["key"])) == 32
    assert oct((paths.service / "mailbox.key").stat().st_mode & 0o777) == "0o600"
    assert oct((paths.service / "mailbox-mac.token").stat().st_mode & 0o777) == "0o600"
    assert "mailbox" not in service.shared_status() and "routes" not in service.shared_status()


def test_unreachable_home_server_is_recorded_not_raised(tmp_path, mac):
    service, _pi, _paths = mac

    def down(*_a, **_k):
        raise mb.MailboxError("home server unreachable: refused")

    service.mailbox._request = down
    assert service.mailbox.cycle()["last_error"].startswith("home server unreachable")


def test_without_configuration_phones_are_told_to_forget_the_mailbox(tmp_path):
    from chewsense.loop.server import LoopService

    service = LoopService(LoopPaths(tmp_path / "loop"), loop_config(), vision_runner=fake_vision(),
                          trainer=lambda _p: None, proposer=lambda *_a: {"summary": {}})
    assert service.mailbox is None and service.phone_status()["mailbox"] is None
