"""A service restart must not leave its isolated Vision worker alive."""
import os
import subprocess
import sys
import time


def test_worker_exits_when_owning_service_process_disappears(tmp_path):
    marker = tmp_path / "terminated"
    child_code = (
        "import signal,time; from pathlib import Path; "
        "from chewsense.loop.process_lifetime import watch_service_parent; "
        f"signal.signal(signal.SIGTERM, lambda *_: (Path({str(marker)!r}).write_text('terminated'), exit(0))); "
        "watch_service_parent(); print('ready',flush=True); time.sleep(10)"
    )
    parent_code = (
        "import os,subprocess,sys; "
        f"child=subprocess.Popen([sys.executable,'-c',{child_code!r}], "
        "env=dict(os.environ,CHEWSENSE_LOOP_PARENT_PID=str(os.getpid())), "
        "start_new_session=True,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,text=True); "
        "assert child.stdout.readline().strip()=='ready'"
    )
    subprocess.run([sys.executable, "-c", parent_code], check=True, timeout=5, env=dict(os.environ))
    deadline = time.monotonic() + 5
    while not marker.exists() and time.monotonic() < deadline:
        time.sleep(.05)
    assert marker.read_text() == "terminated"
