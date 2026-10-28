"""Small Tk window for DiT/LM downloads; the main control panel stays responsive."""
import queue
import threading

from scripts.model_store import ModelStore


def format_size(size):
    return f"{size / 1024 ** 3:.2f} GB" if size >= 1024 ** 3 else f"{size / 1024 ** 2:.1f} MB"


class ModelManager:
    def __init__(self, parent, controller, parent_busy=lambda: False):
        import tkinter as tk
        from tkinter import ttk
        self.controller = controller
        self.parent_busy = parent_busy
        self.store = ModelStore(controller)
        self.busy = False
        self.cancel = threading.Event()
        self.events = queue.Queue()
        self.window = tk.Toplevel(parent)
        self.window.title("Music Studio — Quản lý model")
        self.window.geometry("820x540")
        self.window.minsize(650, 450)
        frame = ttk.Frame(self.window, padding=18)
        frame.pack(fill="both", expand=True)
        ttk.Label(frame, text="Quản lý model DiT / LM", font=("Segoe UI", 16, "bold")).pack(anchor="w")
        ttk.Label(frame, text="Tải model khi cần. Dừng tất cả dịch vụ trước khi tải hoặc xóa.",
                  wraplength=760).pack(anchor="w", pady=(6, 14))
        area = ttk.Frame(frame)
        area.pack(fill="both", expand=True)
        self.table = ttk.Treeview(area, columns=("kind", "name", "status", "size"), show="headings", selectmode="browse")
        for key, label, width in [("kind", "Loại", 50), ("name", "Model", 350),
                                  ("status", "Trạng thái", 120), ("size", "Trên ổ đĩa", 90)]:
            self.table.heading(key, text=label)
            self.table.column(key, width=width, minwidth=50, stretch=key == "name")
        scroll = ttk.Scrollbar(area, orient="vertical", command=self.table.yview)
        self.table.configure(yscrollcommand=scroll.set)
        scroll.pack(side="right", fill="y")
        self.table.pack(side="left", fill="both", expand=True)
        self.message = tk.StringVar(value="Đang đọc danh sách từ ACE-Step…")
        ttk.Label(frame, textvariable=self.message, wraplength=760).pack(anchor="w", pady=12)
        self.progress = ttk.Progressbar(frame, mode="indeterminate")
        self.progress.pack(fill="x", pady=(0, 12))
        buttons = ttk.Frame(frame)
        buttons.pack(fill="x")
        self.download_button = ttk.Button(buttons, text="Tải / tiếp tục tải", command=self.download)
        self.download_button.pack(side="left", padx=(0, 8))
        self.delete_button = ttk.Button(buttons, text="Xóa model đã chọn", command=self.delete)
        self.delete_button.pack(side="left", padx=(0, 8))
        self.refresh_button = ttk.Button(buttons, text="Làm mới", command=self.refresh)
        self.refresh_button.pack(side="left")
        self.cancel_button = ttk.Button(buttons, text="Hủy tải", command=self.cancel.set, state="disabled")
        self.cancel_button.pack(side="right")
        self.window.protocol("WM_DELETE_WINDOW", self.close)
        self.table.bind("<<TreeviewSelect>>", lambda _: self.update_buttons())
        self.after_id = self.window.after(150, self.tick)
        self.refresh()

    def selected(self):
        selected = self.table.selection()
        return next((m for m in self.store.models if m["name"] == selected[0]), None) if selected else None

    def update_buttons(self):
        row = self.selected()
        editable = not self.busy and not self.parent_busy() and not self.controller.processes
        self.download_button.config(state="normal" if editable and row and not row["complete"] and not row.get("local_only") else "disabled")
        self.delete_button.config(state="normal" if editable and row and row["exists"] else "disabled")
        self.refresh_button.config(state="disabled" if self.busy else "normal")

    def run(self, action, downloading=False):
        if self.busy:
            return
        self.busy = True
        self.cancel.clear()
        self.progress.start(12)
        self.cancel_button.config(state="normal" if downloading else "disabled")
        self.update_buttons()
        def work():
            try:
                action()
            except InterruptedError as exc:
                self.events.put(("message", str(exc)))
            except Exception as exc:
                self.events.put(("error", str(exc)))
            finally:
                self.events.put(("done", None))
        threading.Thread(target=work, daemon=True).start()

    def refresh(self):
        def action():
            self.events.put(("rows", self.store.refresh()))
            self.events.put(("message", "Chọn model để tải hoặc xóa. Model chưa tải không chiếm RAM/VRAM."))
        self.run(action)

    def download(self):
        row = self.selected()
        if not row:
            return
        def action():
            self.store.download(row["name"], self.cancel,
                                lambda size: self.events.put(("message", f"Đang tải {row['name']} · đã ghi {format_size(size)}…")))
            self.events.put(("rows", self.store.refresh()))
            self.events.put(("message", "Tải hoàn tất. Bấm Khởi động để chọn model trong giao diện tạo nhạc."))
        self.run(action, downloading=True)

    def delete(self):
        from tkinter import messagebox
        row = self.selected()
        if not row:
            return
        if not messagebox.askyesno("Xóa model", f"Xóa {row['name']} ({format_size(row['bytes'])}) khỏi ổ đĩa?\n"
                                   "Có thể tải lại sau. Nhạc, dataset và LoRA được giữ nguyên.", parent=self.window):
            return
        def action():
            self.store.delete(row["name"])
            self.events.put(("rows", self.store.refresh()))
            self.events.put(("message", f"Đã xóa {row['name']}."))
        self.run(action)

    def close(self):
        from tkinter import messagebox
        if self.busy:
            messagebox.showinfo("Quản lý model", "Hãy hủy tải và chờ tác vụ dừng trước khi đóng cửa sổ.", parent=self.window)
            return
        self.window.after_cancel(self.after_id)
        self.window.destroy()

    def tick(self):
        from tkinter import messagebox
        while not self.events.empty():
            kind, value = self.events.get()
            if kind == "rows":
                selected = self.table.selection()
                self.table.delete(*self.table.get_children())
                for row in value:
                    status = "Đã tải" if row["complete"] else "Tải dở / thiếu file" if row["exists"] else "Chưa tải"
                    self.table.insert("", "end", iid=row["name"], values=(row["kind"], row["name"], status, format_size(row["bytes"])))
                if selected and self.table.exists(selected[0]):
                    self.table.selection_set(selected[0])
            elif kind == "message":
                self.message.set(value)
            elif kind == "error":
                self.message.set(value)
                messagebox.showerror("Quản lý model", value, parent=self.window)
            elif kind == "done":
                self.busy = False
                self.progress.stop()
                self.cancel_button.config(state="disabled")
        self.update_buttons()
        self.after_id = self.window.after(250, self.tick)
