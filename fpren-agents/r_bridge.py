"""
r_bridge.py  —  Python to RStudio bridge
Triggers R stats modules and Rmd report rendering from the director.
"""
import subprocess, logging, json, os
from pathlib import Path
from datetime import datetime

log = logging.getLogger("RBridge")
RSCRIPT    = os.getenv("RSCRIPT_PATH", "Rscript")
AGENTS_DIR = Path(__file__).parent
R_DIR      = AGENTS_DIR / "r_modules"
OUTPUT_DIR = AGENTS_DIR / "reports" / "output"

class RBridge:
    def __init__(self):
        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    def render_daily(self):
        return self._rscript(R_DIR / "render_reports.R", label="daily render")

    def run_module(self, module):
        script = R_DIR / f"{module}.R"
        if not script.exists():
            log.error("R module not found: %s", script)
            return False
        return self._rscript(script, label=module)

    def query_stat(self, fn_name, **kwargs):
        args_r = ", ".join(f"{k}={v}" for k, v in kwargs.items())
        tmp = OUTPUT_DIR / f"_q_{fn_name}_{int(datetime.now().timestamp())}.json"
        r_code = (
            f'source("{R_DIR}/fpren_mongo.R"); '
            f'source("{R_DIR}/alert_stats.R"); '
            f'source("{R_DIR}/weather_stats.R"); '
            f'source("{R_DIR}/traffic_stats.R"); '
            f'source("{R_DIR}/broadcast_stats.R"); '
            f'df <- {fn_name}({args_r}); '
            f'jsonlite::write_json(df, "{tmp}", auto_unbox=TRUE)'
        )
        ok = self._run([RSCRIPT, "--vanilla", "-e", r_code], label=f"query:{fn_name}")
        if not ok or not tmp.exists():
            return []
        result = json.loads(tmp.read_text())
        tmp.unlink(missing_ok=True)
        return result

    def latest_reports(self):
        m = OUTPUT_DIR / "manifest.json"
        return json.loads(m.read_text()) if m.exists() else {}

    def _rscript(self, script, args=None, label=""):
        return self._run([RSCRIPT, "--vanilla", str(script)] + (args or []), label)

    def _run(self, cmd, label):
        log.info("R [%s]", label)
        try:
            r = subprocess.run(cmd, capture_output=True, text=True,
                               cwd=str(AGENTS_DIR), timeout=300)
            if r.returncode != 0:
                log.error("R error [%s]:\n%s", label, r.stderr[-2000:])
                return False
            return True
        except Exception as e:
            log.error("R bridge error [%s]: %s", label, e)
            return False
