from contextlib import nullcontext
from types import SimpleNamespace


from scripts import resources


def test_wddm_keeps_total_vram_and_does_not_invent_process_memory():
    def run(args, **kwargs):
        output = "0, NVIDIA RTX, 12, 5307, 12288\n" if "--query-gpu=" in args[1] else "123, [N/A]\n"
        return SimpleNamespace(returncode=0, stdout=output)
    gpu = resources.gpu_sample(run)
    assert gpu["devices"][0]["used_mb"] == 5307
    assert gpu["processes"][123] is None
    row = dict(key="acestep", vram_mb=None, vram_unknown=True)
    assert resources.component_vram(row, gpu) == "5307 (tổng GPU)"
    assert resources.component_vram({**row, "key": "api"}, gpu) == "N/A"


def test_missing_nvidia_tool_preserves_cpu_monitoring():
    def absent(*args, **kwargs):
        raise FileNotFoundError()
    gpu = resources.gpu_sample(absent)
    assert not gpu["available"]
    assert resources.component_vram(dict(key="acestep", vram_mb=None, vram_unknown=False), gpu) == "—"


def test_gpu_timeout_is_not_reported_as_zero():
    def timeout(*args, **kwargs):
        raise resources.subprocess.TimeoutExpired("nvidia-smi", 3)
    assert resources.gpu_sample(timeout)["devices"] == []


class Process:
    def __init__(self, pid, mb):
        self.pid = pid
        self.mb = mb

    def create_time(self): return 123
    def is_running(self): return True
    def oneshot(self): return nullcontext()
    def cpu_percent(self, interval): return 80
    def memory_info(self): return SimpleNamespace(rss=self.mb * 1024 ** 2)


def test_component_cpu_ram_include_children_without_double_counting(monkeypatch):
    a, child, w = Process(1, 10), Process(2, 20), Process(3, 30)
    controller = SimpleNamespace(processes=[("api", a), ("worker", w)], resource_extras={},
                    process_trees={1: SimpleNamespace(snapshot=lambda: [a, child]),
                                   3: SimpleNamespace(snapshot=lambda: [child, w])})
    monkeypatch.setattr(resources, "gpu_sample", lambda: dict(devices=[], processes={}, available=False))
    monkeypatch.setattr(resources.psutil, "cpu_count", lambda: 4)
    monitor = resources.ResourceMonitor(controller)
    monitor.control = Process(99, 5)
    first = monitor.collect()
    assert next(r for r in first["rows"] if r["key"] == "api")["cpu"] is None
    rows = {r["key"]: r for r in monitor.collect()["rows"]}
    assert rows["api"]["ram_mb"] == 30
    assert rows["api"]["cpu"] == 40
    assert rows["worker"]["ram_mb"] == 30
    assert rows["worker"]["pids"] == [3]
    assert rows["acestep"]["status"] == "Đã dừng"
