"""Do not let an isolated Vision process outlive its owning loop service."""
from __future__ import annotations

import os
import signal
import threading


def watch_service_parent() -> None:
    parent = os.environ.get("CHEWSENSE_LOOP_PARENT_PID")
    if not parent or not parent.isdecimal() or os.getpgrp() != os.getpid():
        return
    expected = int(parent)

    def watch():
        tick = threading.Event()
        while not tick.wait(0.25):
            if os.getppid() != expected:
                os.killpg(os.getpgrp(), signal.SIGTERM)
                return

    threading.Thread(target=watch, daemon=True).start()
