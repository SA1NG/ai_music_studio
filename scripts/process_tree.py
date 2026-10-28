"""Track owned process identities, including Windows launcher descendants."""
import threading

import psutil


class OwnedProcessTree:
    def __init__(self, pid):
        self.lock = threading.RLock()
        self.members = {}
        try:
            self.members[pid] = psutil.Process(pid)
        except psutil.NoSuchProcess:
            pass
        self.refresh()

    def refresh(self):
        with self.lock:
            for process in list(self.members.values()):
                try:
                    # psutil checks process identity, protecting against reused PIDs.
                    if process.is_running():
                        for child in process.children(recursive=True):
                            self.members.setdefault(child.pid, child)
                except psutil.NoSuchProcess:
                    pass

    def is_running(self):
        self.refresh()
        with self.lock:
            return any(p.is_running() for p in self.members.values())

    def snapshot(self):
        self.refresh()
        with self.lock:
            return [p for p in self.members.values() if p.is_running()]

    def stop(self, timeout=15):
        self.refresh()
        with self.lock:
            members = list(self.members.values())
            # Kill children first, even if the original launcher has already exited.
            for process in reversed(members):
                try:
                    process.kill()
                except psutil.NoSuchProcess:
                    pass
            _, alive = psutil.wait_procs(members, timeout=timeout)
            if any(p.is_running() for p in alive):
                raise RuntimeError("Chưa dừng được toàn bộ tiến trình con. Không nạp thêm model.")
