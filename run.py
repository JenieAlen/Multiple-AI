"""
Convenience launcher. Run:

    python run.py

Then open http://127.0.0.1:8000 in your browser.
"""

import os
from pathlib import Path
from dotenv import load_dotenv

# Load .env for local development. On Render, .env won't exist so this is
# a no-op and env vars come from the Render dashboard instead.
load_dotenv(Path(__file__).resolve().parent / ".env")

import uvicorn

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run(
        "backend.main:app",
        host="0.0.0.0",
        port=port,
        reload=False,
    )
