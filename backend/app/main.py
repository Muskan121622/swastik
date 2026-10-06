"""FastAPI entrypoint. `python -m uvicorn app.main:app` from /backend.

In a single-service deployment this same process also serves the built React
app from frontend/dist, so the public URL is one origin and there is no CORS
question to answer. When dist is absent (local dev, CI, tests) only the API is
exposed and the vite dev server proxies to it as before.
"""
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from app.config import STATIC_DIR, ALLOWED_ORIGINS, ALLOW_CREDENTIALS
from app.db.database import init_db, SessionLocal
from app.db.seed import seed
from app.api.routes import router


@asynccontextmanager
async def lifespan(_app: FastAPI):
    init_db()
    db = SessionLocal()
    try:
        seed(db)
    finally:
        db.close()
    yield


app = FastAPI(title="SwasthiQ Front Desk Agent", lifespan=lifespan)

# Split deploy (frontend on Vercel, API here): ALLOWED_ORIGINS lists the SPA
# origin(s). Wildcard default keeps same-origin Docker + local dev working.
app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=ALLOW_CREDENTIALS,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(router)


@app.get("/api/health")
def health():
    return {"ok": True}


# --------------------------------------------------------------------------
# optional SPA hosting (must stay last: /api routes are matched first)
# --------------------------------------------------------------------------
if STATIC_DIR.is_dir():

    app.mount("/assets", StaticFiles(directory=STATIC_DIR / "assets"), name="assets")

    @app.get("/{full_path:path}", include_in_schema=False)
    def spa(full_path: str):
        # A typo under /api must stay a machine-readable 404, never HTML.
        if full_path.startswith("api/"):
            return JSONResponse(
                status_code=404,
                content={"detail": {"code": "ENDPOINT_NOT_FOUND",
                                    "message": "No such API endpoint."}},
            )
        candidate = (STATIC_DIR / full_path).resolve()
        if full_path and candidate.is_file() and candidate.is_relative_to(STATIC_DIR.resolve()):
            return FileResponse(candidate)
        return FileResponse(STATIC_DIR / "index.html")
