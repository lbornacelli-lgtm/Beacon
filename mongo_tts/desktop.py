"""Tkinter desktop monitor for the MongoDB TTS collection."""

import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

import db
import importer
import tts
from config import COLLECTION, DB_NAME, MONGO_URI, WAV_OUTPUT_DIR

REFRESH_SEC = 10


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("MongoDB TTS Monitor")
        self.geometry("1100x650")
        self._build_ui()
        self._schedule_refresh()

    # ------------------------------------------------------------------ UI

    def _build_ui(self):
        # Header
        ttk.Label(self, text="MongoDB TTS Monitor", font=("Arial", 16, "bold")).pack(pady=8)

        info = f"DB: {MONGO_URI}  |  {DB_NAME}.{COLLECTION}  |  WAV → {WAV_OUTPUT_DIR}"
        ttk.Label(self, text=info, foreground="gray").pack()

        # Toolbar
        bar = ttk.Frame(self)
        bar.pack(pady=6)
        ttk.Button(bar, text="Import File…",      command=self._import).pack(side="left", padx=4)
        ttk.Button(bar, text="Convert Selected",  command=self._convert_selected).pack(side="left", padx=4)
        ttk.Button(bar, text="Convert All",       command=self._convert_all).pack(side="left", padx=4)
        ttk.Button(bar, text="Delete Selected",   command=self._delete_selected).pack(side="left", padx=4)
        ttk.Button(bar, text="Refresh",           command=self._refresh).pack(side="left", padx=4)

        # Table
        cols = ("id", "type", "description", "imported_at", "wav")
        self.tree = ttk.Treeview(self, columns=cols, show="headings", selectmode="browse")
        widths = {"id": 200, "type": 120, "description": 380, "imported_at": 160, "wav": 120}
        headers = {"id": "ID", "type": "Type", "description": "Description",
                   "imported_at": "Imported At", "wav": "WAV File"}
        for c in cols:
            self.tree.heading(c, text=headers[c])
            self.tree.column(c, width=widths[c], anchor="w")

        vsb = ttk.Scrollbar(self, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=vsb.set)
        self.tree.pack(fill="both", expand=True, padx=10, side="left")
        vsb.pack(side="left", fill="y", pady=10)

        # Status bar
        self.status_var = tk.StringVar(value="Ready")
        ttk.Label(self, textvariable=self.status_var, foreground="gray",
                  anchor="w").pack(fill="x", padx=10, pady=(0, 6))

    # ------------------------------------------------------------------ Data

    def _refresh(self):
        self.tree.delete(*self.tree.get_children())
        entries = db.all_entries()
        for e in entries:
            desc = (e.get("description") or "")[:80]
            wav  = "✔ " + e["_wav_file"].split("/")[-1] if e.get("_wav_file") else "—"
            self.tree.insert("", "end", iid=str(e["_id"]), values=(
                str(e["_id"]),
                e.get("type", ""),
                desc,
                (e.get("_imported_at") or "")[:19],
                wav,
            ))
        self.status_var.set(f"{len(entries)} entries loaded.")

    def _schedule_refresh(self):
        self._refresh()
        self.after(REFRESH_SEC * 1000, self._schedule_refresh)

    def _selected_id(self):
        sel = self.tree.selection()
        return sel[0] if sel else None

    # ------------------------------------------------------------------ Actions

    def _import(self):
        path = filedialog.askopenfilename(
            title="Select JSON or XML file",
            filetypes=[("JSON / XML", "*.json *.xml"), ("All files", "*.*")]
        )
        if not path:
            return
        self.status_var.set("Importing…")
        self.update_idletasks()

        def task():
            try:
                result = importer.import_file(path)
                msg = f"Imported {result['imported']}, converted {result['converted']}, failed {result['failed']}"
            except Exception as e:
                msg = f"Import error: {e}"
            self.after(0, lambda: [self.status_var.set(msg), self._refresh()])

        threading.Thread(target=task, daemon=True).start()

    def _convert_selected(self):
        eid = self._selected_id()
        if not eid:
            messagebox.showinfo("No selection", "Select an entry first.")
            return
        self.status_var.set("Converting…")
        self.update_idletasks()

        def task():
            try:
                entry = db.get_entry(eid)
                wav = tts.convert_entry(entry)
                db.update_wav(eid, wav)
                msg = f"Converted → {wav.name}"
            except Exception as e:
                msg = f"Error: {e}"
            self.after(0, lambda: [self.status_var.set(msg), self._refresh()])

        threading.Thread(target=task, daemon=True).start()

    def _convert_all(self):
        self.status_var.set("Converting all…")
        self.update_idletasks()

        def task():
            entries = db.all_entries()
            converted = failed = 0
            for entry in entries:
                if entry.get("description"):
                    try:
                        wav = tts.convert_entry(entry)
                        db.update_wav(entry["_id"], wav)
                        converted += 1
                    except Exception as e:
                        print(f"TTS failed for {entry['_id']}: {e}")
                        failed += 1
            msg = f"Done — converted {converted}, failed {failed}"
            self.after(0, lambda: [self.status_var.set(msg), self._refresh()])

        threading.Thread(target=task, daemon=True).start()

    def _delete_selected(self):
        eid = self._selected_id()
        if not eid:
            messagebox.showinfo("No selection", "Select an entry first.")
            return
        if messagebox.askyesno("Confirm", f"Delete entry {eid}?"):
            db.delete_entry(eid)
            self._refresh()


if __name__ == "__main__":
    App().mainloop()
