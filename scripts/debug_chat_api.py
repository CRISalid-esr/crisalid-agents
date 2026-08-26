import os
import sys
from pathlib import Path

import uvicorn
from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[1]

sys.path.insert(0, str(PROJECT_ROOT))

load_dotenv(PROJECT_ROOT / ".env")

os.environ.setdefault("PORT", "9100")
os.environ.setdefault("HOST", "0.0.0.0")

if __name__ == "__main__":
    uvicorn.run(
        "chat_api.main:app",
        host=os.environ["HOST"],
        port=int(os.environ["PORT"]),
        # reload spawns a subprocess, which detaches the IDE debugger
        reload=False,
    )
