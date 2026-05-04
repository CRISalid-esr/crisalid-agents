import os
import sys
from pathlib import Path

import uvicorn
from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[1]
VENDOR_DIR = PROJECT_ROOT / ".openwebui-pipelines"
PIPELINES_DIR = PROJECT_ROOT / "openwebui_pipelines"

# Equivalent to launching from .openwebui-pipelines
os.chdir(VENDOR_DIR)

# Make both projects importable
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(VENDOR_DIR))

# Load your app config
load_dotenv(PROJECT_ROOT / ".env")

# Same defaults as start.sh
os.environ.setdefault("PORT", "9099")
os.environ.setdefault("HOST", "0.0.0.0")
os.environ.setdefault("PIPELINES_DIR", str(PIPELINES_DIR))
os.environ.setdefault(
    "PIPELINES_API_KEY",
    "0p3n-w3bu!"  # Default key from openwebui tutorials, not a security breach
)
os.environ.setdefault("UVICORN_LOOP", "auto")

if __name__ == "__main__":
    uvicorn.run(
        "main:app",
        host=os.environ["HOST"],
        port=int(os.environ["PORT"]),
        forwarded_allow_ips="*",
        loop=os.environ["UVICORN_LOOP"],
        reload=False,
    )
