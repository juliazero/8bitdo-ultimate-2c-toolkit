# SPDX-License-Identifier: GPL-3.0-or-later
"""One background job at a time, with an append-only event list that Server-Sent Events replay."""
import os
import secrets
import subprocess
import threading
from types import SimpleNamespace

from .. import cli, fetch, state
from ..ops.log import get_logger
from ..ops.plan import OpError
from ..transport import PermissionDenied

# job name -> function(args, store); tests replace entries
ACTIONS = {"backup": cli.cmd_backup, "patch": cli.cmd_patch, "restore": cli.cmd_restore}


def keep_awake():
    """Keeps the Mac from sleeping while idle, which would stop the USB connection mid-write, until stopped or until
    this process ends. None where caffeinate doesn't exist."""
    try:
        return subprocess.Popen(["/usr/bin/caffeinate", "-i", "-w", str(os.getpid())])
    except OSError:
        return None


class Runner:
    def __init__(self, store):
        self.store, self.events, self.running, self.lock, self.awake = store, [], False, None, None
        self.writing = False   # a job that writes to the controller is running: the Mac app asks before quitting
        self.closing = False
        self.cond = threading.Condition()
        self.job_id = None
        self.results = {}   # recent terminal results survive stream disconnects and later jobs

    def status(self, job_id):
        with self.cond:
            if job_id in self.results:
                return {"running": False, "result": self.results[job_id]}
            if job_id and job_id == self.job_id:
                return {"running": self.running, "result": None}
            return None

    def push(self, event: dict) -> None:
        with self.cond:
            self.events.append(event)
            self.cond.notify_all()

    def _take(self) -> bool:
        """With self.cond held: the device for this server, unless a job here or another process is using it."""
        if self.running or self.closing:
            return False
        self.lock = state.lock_device(self.store)
        self.running = self.lock is not None
        return self.running

    def request_shutdown(self):
        """Stop accepting work; existing operations retain their device lock until finished."""
        with self.cond:
            self.closing = True
            self.cond.notify_all()

    def _free(self) -> None:
        state.unlock_device(self.lock)
        if self.awake:
            self.awake.terminate()
        self.running, self.writing, self.awake = False, False, None
        self.cond.notify_all()

    WAIT = 3.0   # s: another short read in this core (the battery's, every 30 s) ends well within it

    def claim(self) -> bool:
        """Take the device for a short read outside the job system (the firmware read); False if it is in use. A short
        read already running in this core is waited for, so polling the battery doesn't turn the page's reads away."""
        with self.cond:
            self.cond.wait_for(lambda: not self.running, timeout=self.WAIT)
            return self._take()

    def release(self) -> None:
        with self.cond:
            self._free()

    def start(self, name: str, **params) -> str | bool:
        with self.cond:
            self.cond.wait_for(lambda: not self.running, timeout=self.WAIT)
            if not self._take():
                return False
            self.events, self.writing = [], bool(params.get("write"))
            job_id = self.job_id = secrets.token_urlsafe(16)
        try:   # until the thread runs, nothing else frees the device
            if self.writing:
                self.awake = keep_awake()
            args = SimpleNamespace(**{**dict(write=False, tweaks="", backup=None, download=False, source="backup", expect=None, bt_name=None, cable_name=None, xinput_name=None, dinput_name=None,
                                             accept_disclaimer=False, force_unknown=False, device=None), **params})
            log = get_logger(self.store)
            log.info("job %s %s", name, params)   # the page never sends a path

            def say(text):
                log.info("  %s", text)
                args.last = str(text)   # the last sentence is the task's result
                self.push({"type": "log", "text": str(text)})
            args.say = say
            args.hint = lambda text: None  # command-line advice, meaningless in the app
            args.progress = lambda label, done, total: self.push({"type": "progress", "label": label, "done": done, "total": total})
            threading.Thread(target=self._run, args=(name, args), daemon=True).start()
        except BaseException:
            self.release()
            raise
        return job_id

    def _run(self, name, args):
        try:
            ACTIONS[name](args, self.store)
            end = {"type": "done", "message": getattr(args, "last", ""), "unchanged": getattr(args, "unchanged", False)}
        except cli.Exit as e:
            end = {"type": "error", "message": e.msg}
        except (OpError, fetch.FetchError, PermissionDenied) as e:
            end = {"type": "error", "message": str(e)}
        except Exception as e:  # shown to the user, never a hang
            end = {"type": "error", "message": f"{type(e).__name__}: {e}"}
        get_logger(self.store).info("done" if end["type"] == "done" else f"error: {end['message']}")
        with self.cond:
            self.events.append(end)
            self.results[self.job_id] = end
            while len(self.results) > 8:
                del self.results[next(iter(self.results))]
            self._free()
