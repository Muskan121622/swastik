"""Central configuration. Env is loaded once; the LLM provider is pluggable."""
import os
from pathlib import Path
from dotenv import load_dotenv

BACKEND_DIR = Path(__file__).resolve().parent.parent
ROOT_DIR = BACKEND_DIR.parent
load_dotenv(BACKEND_DIR / ".env")

DATA_DIR = BACKEND_DIR / "data"
DATA_DIR.mkdir(exist_ok=True)

DATABASE_URL = os.getenv("DATABASE_URL", f"sqlite:///{DATA_DIR / 'clinic.db'}")

# A sqlite file on a mounted volume (e.g. Render's /render disk) needs its
# parent directory to exist before the engine connects.
if DATABASE_URL.startswith("sqlite"):
    _sqlite_path = Path(DATABASE_URL.split("///", 1)[-1])
    _sqlite_path.parent.mkdir(parents=True, exist_ok=True)

# Single-service deploy: FastAPI also serves the built React app, if present.
STATIC_DIR = ROOT_DIR / "frontend" / "dist"

GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")
GROQ_MODEL = os.getenv("GROQ_MODEL", "openai/gpt-oss-120b")

# Deterministic guardrails for the agent loop
MAX_TOOL_CALLS_PER_TURN = 4
