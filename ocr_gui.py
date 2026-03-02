import os
import re
import subprocess
import threading
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

APP_TITLE = "AstraWorks · PDF → OCR + TXT pulito"

def clean_text(text: str) -> str:
    # 1) unisci parole spezzate con trattino a fine riga: "par-\nola" -> "parola"
    text = re.sub(r"(\w+)-\n(\w+)", r"\1\2", text)

    # 2) trasforma i singoli a-capo in spazio (lascia i doppi a-capo = cambio paragrafo)
    text = re.sub(r"(?<!\n)\n(?!\n)", " ", text)

    # 3) normalizza spazi multipli
    text = re.sub(r"[ ]{2,}", " ", text)

    # 4) normalizza troppi a-capo
    text = re.sub(r"\n{3,}", "\n\n", text)

    return text.strip() + "\n"

def which_or_hint(cmd: str) -> tuple[bool, str]:
    # Su Windows: 'where', su Linux/macOS: 'which'
    finder = "where" if os.name == "nt" else "which"
    try:
        p = subprocess.run([finder, cmd], capture_output=True, text=True)
        ok = p.returncode == 0
        return ok, p.stdout.strip() or p.stderr.strip()
    except Exception as e:
        return False, str(e)

def run_ocrmypdf(input_pdf: Path, out_dir: Path, lang: str, skip_text: bool, log_cb):
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = input_pdf.stem

    out_pdf = out_dir / f"{stem}_ocr.pdf"
    sidecar_txt = out_dir / f"{stem}.txt"
    clean_txt = out_dir / f"{stem}_clean.txt"

    cmd = [
        "ocrmypdf",
        "-l", lang,
        "--sidecar", str(sidecar_txt),
    ]

    if skip_text:
        cmd.append("--skip-text")

    # NB: input e output
    cmd += [str(input_pdf), str(out_pdf)]

    log_cb(f"Comando:\n{' '.join(cmd)}\n")

    # esegui OCRmyPDF
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
    )

    out_lines = []
    for line in proc.stdout:
        out_lines.append(line)
        log_cb(line)

    code = proc.wait()
    if code != 0:
        raise RuntimeError(f"OCRmyPDF è terminato con exit code {code}")

    # pulizia TXT
    if sidecar_txt.exists():
        raw = sidecar_txt.read_text(encoding="utf-8", errors="replace")
        cleaned = clean_text(raw)
        clean_txt.write_text(cleaned, encoding="utf-8")
    else:
        raise RuntimeError("Sidecar TXT non trovato: OCRmyPDF non ha generato il .txt")

    return out_pdf, sidecar_txt, clean_txt

class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(APP_TITLE)
        self.geometry("900x520")

        self.input_path = tk.StringVar(value="")
        self.output_dir = tk.StringVar(value="")
        self.lang = tk.StringVar(value="ita")
        self.skip_text = tk.BooleanVar(value=True)

        self._build_ui()
        self._check_deps()

    def _build_ui(self):
        pad = {"padx": 10, "pady": 6}

        frm = ttk.Frame(self)
        frm.pack(fill="x", **pad)

        ttk.Label(frm, text="PDF input:").grid(row=0, column=0, sticky="w")
        ttk.Entry(frm, textvariable=self.input_path, width=80).grid(row=0, column=1, sticky="we")
        ttk.Button(frm, text="Sfoglia…", command=self.pick_input).grid(row=0, column=2)

        ttk.Label(frm, text="Cartella output:").grid(row=1, column=0, sticky="w")
        ttk.Entry(frm, textvariable=self.output_dir, width=80).grid(row=1, column=1, sticky="we")
        ttk.Button(frm, text="Scegli…", command=self.pick_output).grid(row=1, column=2)

        opt = ttk.Frame(self)
        opt.pack(fill="x", **pad)

        ttk.Label(opt, text="Lingua OCR (es: ita, eng):").grid(row=0, column=0, sticky="w")
        ttk.Entry(opt, textvariable=self.lang, width=10).grid(row=0, column=1, sticky="w", padx=(6, 20))

        ttk.Checkbutton(opt, text="--skip-text (consigliato)", variable=self.skip_text).grid(row=0, column=2, sticky="w")

        btns = ttk.Frame(self)
        btns.pack(fill="x", **pad)

        self.run_btn = ttk.Button(btns, text="Esegui OCR + TXT pulito", command=self.start)
        self.run_btn.pack(side="left")

        ttk.Button(btns, text="Pulisci log", command=self.clear_log).pack(side="left", padx=10)

        self.status = tk.StringVar(value="Pronto.")
        ttk.Label(self, textvariable=self.status).pack(fill="x", **pad)

        self.log = tk.Text(self, wrap="word")
        self.log.pack(fill="both", expand=True, padx=10, pady=10)
        self._log("AstraWorks online. Seleziona un PDF e una cartella output.\n")

    def _check_deps(self):
        ok_ocr, msg_ocr = which_or_hint("ocrmypdf")
        if not ok_ocr:
            self._log("⚠️ ocrmypdf non trovato in PATH.\n")
        else:
            self._log("✅ ocrmypdf trovato.\n")

        # tesseract è richiesto per fare OCR vero
        ok_tes, msg_tes = which_or_hint("tesseract")
        if not ok_tes:
            self._log("⚠️ tesseract non trovato in PATH (OCR potrebbe fallire).\n")
        else:
            self._log("✅ tesseract trovato.\n")

        # ghostscript è “optional” ma evita alcuni warning/ottimizzazioni
        # su Windows spesso è gswin64c
        gs_cmd = "gswin64c" if os.name == "nt" else "gs"
        ok_gs, _ = which_or_hint(gs_cmd)
        if not ok_gs:
            self._log("ℹ️ Ghostscript non trovato (ok, ma avrai warning su ottimizzazione immagini).\n")
        else:
            self._log("✅ Ghostscript trovato.\n")

        self._log("\n")

    def pick_input(self):
        p = filedialog.askopenfilename(
            title="Seleziona un PDF",
            filetypes=[("PDF", "*.pdf"), ("All files", "*.*")]
        )
        if p:
            self.input_path.set(p)

    def pick_output(self):
        d = filedialog.askdirectory(title="Scegli cartella di output")
        if d:
            self.output_dir.set(d)

    def clear_log(self):
        self.log.delete("1.0", "end")

    def _log(self, s: str):
        self.log.insert("end", s)
        self.log.see("end")
        self.update_idletasks()

    def start(self):
        inp = self.input_path.get().strip()
        out = self.output_dir.get().strip()
        lang = self.lang.get().strip() or "ita"

        if not inp:
            messagebox.showerror("Errore", "Seleziona un PDF input.")
            return
        if not out:
            messagebox.showerror("Errore", "Seleziona una cartella di output.")
            return

        input_pdf = Path(inp)
        out_dir = Path(out)

        if not input_pdf.exists():
            messagebox.showerror("Errore", "Il PDF input non esiste.")
            return

        self.run_btn.config(state="disabled")
        self.status.set("Sto lavorando…")

        def worker():
            try:
                self._log("\n--- START ---\n")
                out_pdf, sidecar_txt, clean_txt = run_ocrmypdf(
                    input_pdf=input_pdf,
                    out_dir=out_dir,
                    lang=lang,
                    skip_text=self.skip_text.get(),
                    log_cb=self._log,
                )
                self._log("\n--- DONE ---\n")
                self._log(f"Creati:\n- {out_pdf}\n- {sidecar_txt}\n- {clean_txt}\n")
                self.status.set("Finito ✅")
                messagebox.showinfo("Fatto", f"Creati:\n{out_pdf.name}\n{sidecar_txt.name}\n{clean_txt.name}")
            except Exception as e:
                self.status.set("Errore ❌")
                self._log(f"\nERRORE: {e}\n")
                messagebox.showerror("Errore", str(e))
            finally:
                self.run_btn.config(state="normal")

        threading.Thread(target=worker, daemon=True).start()

if __name__ == "__main__":
    App().mainloop()