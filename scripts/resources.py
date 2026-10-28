"""Read-only resource sampling. NVIDIA queries run outside the Tk event loop."""
import csv
import os
import subprocess
import threading
import time

import psutil


def number(value):
    try:
        return float(value.strip())
    except (ValueError, AttributeError):
        return None


def gpu_sample(run=subprocess.run):
    def query(fields, kind):
        result = run(["nvidia-smi", f"--query-{kind}={fields}", "--format=csv,noheader,nounits"],
                     capture_output=True, text=True, timeout=3,
                     creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        if result.returncode:
            raise OSError("NVIDIA metrics unavailable")
        return list(csv.reader(result.stdout.splitlines(), skipinitialspace=True))
    try:
        devices = []
        for row in query("index,name,utilization.gpu,memory.used,memory.total", "gpu"):
            if len(row) == 5:
                devices.append(dict(index=row[0].strip(), name=row[1].strip(), percent=number(row[2]),
                                    used_mb=number(row[3]), total_mb=number(row[4])))
    except (OSError, subprocess.TimeoutExpired):
        return dict(devices=[], processes={}, available=False)
    processes = {}
    try:
        for row in query("pid,used_gpu_memory", "compute-apps"):
            if len(row) != 2:
                continue
            pid = number(row[0])
            if pid is None:
                continue
            pid, memory = int(pid), number(row[1])
            if memory is None or (pid in processes and processes[pid] is None):
                processes[pid] = None
            else:
                processes[pid] = processes.get(pid, 0) + memory
    except (OSError, subprocess.TimeoutExpired):
        pass
    return dict(devices=devices, processes=processes, available=bool(devices))


def component_vram(row, gpu):
    if row["key"] == "acestep" and gpu["devices"]:
        values = [device["used_mb"] for device in gpu["devices"]]
        if all(value is not None for value in values):
            return f"{sum(values):.0f} (tổng GPU)"
    if row["vram_mb"] is not None:
        return f"{row['vram_mb']:.0f}"
    return "N/A" if row["vram_unknown"] else "—"


class ResourceMonitor:
    GROUPS = [("control", "Điều khiển"), ("ui", "Giao diện (UI)"), ("api", "API"),
              ("worker", "Worker"), ("acestep", "ACE-Step (DiT + LM)"), ("download", "Tải model")]

    def __init__(self, controller):
        self.controller = controller
        self.control = psutil.Process(os.getpid())
        self.cpu_seen = set()
        self.stop_event = threading.Event()
        self.thread = None

    def collect(self):
        gpu = gpu_sample()
        groups = {"control": [self.control]}
        for name, process in list(self.controller.processes):
            tree = self.controller.process_trees.get(process.pid)
            if tree:
                try:
                    groups.setdefault(name, []).extend(tree.snapshot())
                except (psutil.Error, OSError):
                    pass
        for name, tree in list(self.controller.resource_extras.items()):
            try:
                groups.setdefault(name, []).extend(tree.snapshot())
            except (psutil.Error, OSError):
                pass
        rows, seen, cpu_seen = [], set(), set()
        cores = psutil.cpu_count() or 1
        for name, title in self.GROUPS:
            pids, ram, cpu, warming, inaccessible = [], 0, 0, False, False
            for process in groups.get(name, []):
                try:
                    identity = (process.pid, process.create_time())
                    if identity in seen or not process.is_running():
                        continue
                    seen.add(identity)
                    pids.append(process.pid)
                    with process.oneshot():
                        cpu += process.cpu_percent(None) / cores
                        ram += process.memory_info().rss
                    warming |= identity not in self.cpu_seen
                    cpu_seen.add(identity)
                except psutil.NoSuchProcess:
                    pass
                except (psutil.AccessDenied, OSError):
                    inaccessible = True
            values = [gpu["processes"][pid] for pid in pids if pid in gpu["processes"]]
            vram = None if not values or any(value is None for value in values) else sum(values)
            rows.append(dict(key=name, title=title, pids=pids,
                             status="Thiếu quyền đo" if inaccessible else "Đang chạy" if pids else "Đã dừng",
                             cpu=None if warming or inaccessible else cpu,
                             ram_mb=None if inaccessible else ram / 1024 ** 2,
                             vram_mb=vram, vram_unknown=bool(values) and vram is None))
        self.cpu_seen = cpu_seen
        memory = psutil.virtual_memory()
        return dict(rows=rows, gpu=gpu, cpu=psutil.cpu_percent(None), ram_percent=memory.percent,
                    ram_used_gb=(memory.total - memory.available) / 1024 ** 3,
                    ram_total_gb=memory.total / 1024 ** 3, updated=time.strftime("%H:%M:%S"))

    def start(self, deliver):
        def work():
            while not self.stop_event.is_set():
                try:
                    deliver(self.collect())
                except (psutil.Error, OSError, ValueError):
                    pass
                self.stop_event.wait(2)
        self.thread = threading.Thread(target=work, daemon=True)
        self.thread.start()

    def stop(self):
        self.stop_event.set()


class ResourcePanel:
    def __init__(self, parent):
        import tkinter as tk
        from tkinter import ttk
        frame = ttk.LabelFrame(parent, text="Tài nguyên đang sử dụng", padding=10)
        frame.pack(fill="both", expand=True, pady=(8, 0))
        self.summary = tk.StringVar(value="Đang đo tài nguyên…")
        ttk.Label(frame, textvariable=self.summary, wraplength=760).pack(anchor="w", pady=(0, 8))
        self.table = ttk.Treeview(frame, columns=("part", "status", "cpu", "ram", "vram", "pids"),
                                  show="headings", height=6, selectmode="none")
        for key, title, width in [("part", "Thành phần", 165), ("status", "Trạng thái", 95),
                                  ("cpu", "CPU (% máy)", 95), ("ram", "RAM RSS (MB)", 100),
                                  ("vram", "VRAM (MB)", 155), ("pids", "PID", 100)]:
            self.table.heading(key, text=title)
            self.table.column(key, width=width, minwidth=65, stretch=key in ("part", "pids"))
        self.table.pack(fill="both", expand=True)
        ttk.Label(frame, text="CPU/RAM cộng cả tiến trình con. VRAM dòng ACE-Step là tổng GPU, gồm cả ứng dụng khác; "
                  "không phải VRAM riêng của model.", wraplength=760).pack(anchor="w", pady=(8, 0))

    def update(self, sample):
        summary = (f"Toàn máy · CPU {sample['cpu']:.0f}% · RAM {sample['ram_used_gb']:.1f} / "
                   f"{sample['ram_total_gb']:.1f} GB ({sample['ram_percent']:.0f}%)")
        for gpu in sample["gpu"]["devices"]:
            use = f"{gpu['percent']:.0f}%" if gpu["percent"] is not None else "—"
            memory = (f"{gpu['used_mb']:.0f}/{gpu['total_mb']:.0f} MB"
                      if gpu["used_mb"] is not None and gpu["total_mb"] is not None else "—")
            summary += f"\nGPU {gpu['index']} · {gpu['name']} · {use} · VRAM {memory}"
        if not sample["gpu"]["available"]:
            summary += "\nKhông có số liệu NVIDIA GPU; CPU/RAM vẫn được cập nhật."
        self.summary.set(summary + f"\nCập nhật: {sample['updated']}")
        for row in sample["rows"]:
            cpu = f"{row['cpu']:.1f}" if row["cpu"] is not None else "Đang đo"
            ram = f"{row['ram_mb']:.1f}" if row["ram_mb"] is not None else "—"
            vram = component_vram(row, sample["gpu"])
            values = (row["title"], row["status"], cpu, ram, vram, ", ".join(map(str, row["pids"])) or "—")
            if self.table.exists(row["key"]):
                self.table.item(row["key"], values=values)
            else:
                self.table.insert("", "end", iid=row["key"], values=values)
