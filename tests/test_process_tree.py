import subprocess
import sys

import psutil

from scripts.process_tree import OwnedProcessTree


def test_child_is_stopped_even_after_launcher_exits():
    code = ("import subprocess,sys; "
            "p=subprocess.Popen([sys.executable,'-c','import time; time.sleep(60)']); "
            "print(p.pid,flush=True); sys.stdin.readline()")
    parent = subprocess.Popen([sys.executable, "-c", code], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                              text=True, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    tree = None
    try:
        child_pid = int(parent.stdout.readline().strip())
        tree = OwnedProcessTree(parent.pid)
        assert child_pid in tree.members
        parent.stdin.write("\n")
        parent.stdin.flush()
        parent.wait(timeout=10)
        assert psutil.pid_exists(child_pid)
        tree.stop(timeout=10)
        assert not psutil.pid_exists(child_pid)
    finally:
        if tree:
            tree.stop(timeout=10)
        elif parent.poll() is None:
            parent.kill()
        parent.stdin.close()
        parent.stdout.close()
