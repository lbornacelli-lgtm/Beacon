"""Tkinter desktop monitor for the MongoDB TTS collection.
Syncs with the web dashboard every 5 seconds via shared MongoDB collection.
Supports ElevenLabs TTS if ELEVENLABS_API_KEY is set in environment.
"""

import os
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

import db
import importer
import tts
from config import COLLECTION, DB_NAME, MONGO_URI, WAV_OUTPUT_DIR

REFRESH_SEC = 5  # Sync with web dashboard every 5 seconds


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("FPREN - MongoDB TTS Monitor")
        self.geometry("1200x700")
        self._build_ui()
        self._schedule_refresh()

    # ------------------------------------------------------------------ UI

    def _build_ui(self):
        # Header
        header = ttk.Frame(self)
        header.pack(fill="x", padx=10, pady=8)

        ttk.Label(header, text="FPREN - MongoDB TTS Monitor",
                  font=("Arial", 16, "bold")).pack(side="left")

        # TTS engine indicator
        engine = tts.tts_engine_name()
        engine_color = "#198754" if "ElevenLabs" in engine else "#6c757d"
        tk.Label(header, text=f"TTS: {engine}", fg=engine_color,
                 font=("Arial", 10, "bold")).pack(side="right", padx=10)

        # Connection info
        info = f"DB: {MONGO_URI}  |  {DB_NAME}.{COLLECTION}  |  WAV → {WAV_OUTPUT_DIR}"
        ttk.Label(self, text=info, foreground="gray").pack()

        # Sync indicator
        self.sync_var = tk.StringVar(value="⟳ Syncing every 5s with web dashboard")
        ttk.Label(self, textvariable=self.sync_var,
                  foreground="#0d6efd").pack()

        # Toolbar
        bar = ttk.Frame(self)
        bar.pack(pady=6)
        ttk.Button(bar, text="Import File…",      command=self._import).pack(side="left", padx=4)
        ttk.Button(bar, text="Convert Selected",  command=self._convert_selected).pack(side="left", padx=4)
        ttk.Button(bar, text="Convert All",       command=self._convert_all).pack(side="left", padx=4)
        ttk.Button(bar, text="Delete Selected",   command=self._delete_selected).pack(side="left", padx=4)
        ttk.Button(bar, text="Refresh Now",       command=self._refresh).pack(side="left", padx=4)

        # Table
        cols = ("id", "type", "description", "imported_at", "wav")
        self.tree = ttk.Treeview(self, columns=cols, show="headings", selectmode="browse")
        widths = {"id": 200, "type": 120, "description": 400, "imported_at": 160, "wav": 120}
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
        from datetime import datetime
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
        now = datetime.now().strftime("%H:%M:%S")
        self.status_var.set(f"{len(entries)} entries — last synced at {now}")
        self.sync_var.set(f"⟳ Last synced: {now} — refreshing every {REFRESH_SEC}s")

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
        self.status_var.set(f"Converting with {tts.tts_engine_name()}…")
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
        self.status_var.set(f"Converting all with {tts.tts_engine_name()}…")
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
