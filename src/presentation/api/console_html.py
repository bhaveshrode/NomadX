import os
from pathlib import Path

STATIC_DIR = Path(__file__).parent.parent / "static"
CONSOLE_FILE = STATIC_DIR / "console.html"

def get_console_html() -> str:
    """Reads the static console HTML template."""
    if CONSOLE_FILE.exists():
        return CONSOLE_FILE.read_text(encoding="utf-8")
    return "<html><body><h1>NomadX Console - Template Not Found</h1></body></html>"

CONSOLE_HTML = get_console_html()
