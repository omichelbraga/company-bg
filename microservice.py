"""
Photo Background Replacement Microservice
- POST /process-image/ (Bearer auth, rate-limited by email) → returns job_id
- GET /status/{job_id} (Bearer auth) → poll for results
- GET /backgrounds → list available backgrounds
- GET /health → service health
- Images stored in out_images/{email_username}/ — auto-cleaned by APScheduler
"""

import gc
import logging
import os
import stat
import shutil
import uuid
import urllib.parse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from io import BytesIO
from threading import Lock
from typing import Optional

import requests as http_requests
from apscheduler.schedulers.background import BackgroundScheduler
from dotenv import load_dotenv
from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from fastapi.staticfiles import StaticFiles
from PIL import Image

from processor import (
    load_backgrounds,
    detect_face,
    remove_background,
    build_portrait,
    composite_on_background,
)
from graph_client import GraphError, get_user_profile_by_email
from tbg_processor import TeamsBackgroundError, generate_teams_backgrounds

# ── Logging ──────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)

load_dotenv()

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Config ───────────────────────────────────────────────────────────────────
BACKGROUND_DIR = "backgrounds/"
OUTPUT_DIR = "out_images/"
MAX_FILE_SIZE_BYTES = 10 * 1024 * 1024  # 10 MB
ALLOWED_EXTENSIONS = {".jpg", ".jpeg", ".png"}

# Configurable via .env
TOKEN = os.getenv("TOKEN")
CLEANUP_AGE_MINUTES = int(os.getenv("CLEANUP_AGE_MINUTES", "5"))
CLEANUP_AGE_SECONDS = CLEANUP_AGE_MINUTES * 60
CLEANUP_INTERVAL_MINUTES = int(os.getenv("CLEANUP_INTERVAL_MINUTES", "5"))
MAX_REQUESTS = int(os.getenv("RATE_LIMIT_MAX_REQUESTS", "5"))
RATE_LIMIT_WINDOW_MINUTES = int(os.getenv("RATE_LIMIT_WINDOW_MINUTES", "1"))
TIME_WINDOW = timedelta(minutes=RATE_LIMIT_WINDOW_MINUTES)
JOB_EXPIRY_MINUTES = int(os.getenv("JOB_EXPIRY_MINUTES", "10"))
JOB_EXPIRY_SECONDS = JOB_EXPIRY_MINUTES * 60

# ── State ────────────────────────────────────────────────────────────────────
backgrounds: list = []
jobs: dict = {}
jobs_lock = Lock()
rate_limit_store: dict = {}
executor = ThreadPoolExecutor(max_workers=1)  # ONNX session is not concurrent-safe; serialize all jobs

os.makedirs(OUTPUT_DIR, exist_ok=True)
app.mount("/images", StaticFiles(directory=OUTPUT_DIR), name="images")

# ── APScheduler ──────────────────────────────────────────────────────────────
scheduler = BackgroundScheduler()


def cleanup_old_output_folders():
    """Delete subfolders in out_images/ older than 5 minutes."""
    now = datetime.now().timestamp()
    deleted = 0
    try:
        for entry in os.scandir(OUTPUT_DIR):
            if entry.is_dir():
                age = now - entry.stat().st_mtime
                if age > CLEANUP_AGE_SECONDS:
                    for root, _dirs, files in os.walk(entry.path):
                        for f in files:
                            os.chmod(os.path.join(root, f), stat.S_IWRITE)
                    shutil.rmtree(entry.path)
                    deleted += 1
    except Exception as e:
        logger.error(f"Cleanup error: {e}")
    if deleted:
        logger.info(f"Cleanup: removed {deleted} expired output folder(s)")


def cleanup_expired_jobs():
    """Remove jobs older than JOB_EXPIRY_SECONDS from memory."""
    now = datetime.now()
    expired = []
    with jobs_lock:
        for job_id, job in jobs.items():
            if (now - job["created_at"]).total_seconds() > JOB_EXPIRY_SECONDS:
                expired.append(job_id)
        for job_id in expired:
            del jobs[job_id]
    if expired:
        logger.info(f"Cleanup: expired {len(expired)} job(s) from memory")


scheduler.add_job(cleanup_old_output_folders, "interval", minutes=CLEANUP_INTERVAL_MINUTES)
scheduler.add_job(cleanup_expired_jobs, "interval", minutes=CLEANUP_INTERVAL_MINUTES)


# ── Startup / Shutdown ───────────────────────────────────────────────────────
@app.on_event("startup")
async def startup():
    global backgrounds
    backgrounds = load_backgrounds(BACKGROUND_DIR)
    logger.info(f"Loaded {len(backgrounds)} backgrounds")
    scheduler.start()
    logger.info("APScheduler started (cleanup every 5 min)")


@app.on_event("shutdown")
async def shutdown():
    scheduler.shutdown(wait=False)
    executor.shutdown(wait=False)
    logger.info("Scheduler and executor shut down")


# ── Auth ─────────────────────────────────────────────────────────────────────
security = HTTPBearer()


def authorize(credentials: HTTPAuthorizationCredentials = Depends(security)):
    if credentials.credentials != TOKEN:
        raise HTTPException(status_code=401, detail="Unauthorized. Invalid token.")


# ── Rate limiting ────────────────────────────────────────────────────────────
def check_rate_limit(email: str) -> bool:
    now = datetime.now()
    if email not in rate_limit_store:
        rate_limit_store[email] = []
    rate_limit_store[email] = [
        t for t in rate_limit_store[email] if now - t < TIME_WINDOW
    ]
    if len(rate_limit_store[email]) >= MAX_REQUESTS:
        return False
    rate_limit_store[email].append(now)
    return True


# ── Request ID middleware ────────────────────────────────────────────────────
@app.middleware("http")
async def add_request_id(request: Request, call_next):
    request_id = uuid.uuid4().hex[:8]
    request.state.request_id = request_id
    response = await call_next(request)
    response.headers["X-Request-ID"] = request_id
    return response


# ── Helpers ──────────────────────────────────────────────────────────────────
def _short_id() -> str:
    return uuid.uuid4().hex[:8]


def parse_bool(value: Optional[str]) -> bool:
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


async def download_image_from_url(image_url: str) -> BytesIO:
    headers = {"User-Agent": "Mozilla/5.0"}
    try:
        r = http_requests.get(image_url, headers=headers, timeout=15)
        r.raise_for_status()
        return BytesIO(r.content)
    except http_requests.RequestException as e:
        raise HTTPException(
            status_code=400, detail=f"Error downloading image: {str(e)}"
        )


# ── Background job worker ───────────────────────────────────────────────────
def _process_job(job_id: str, raw: bytes, name: str, email: str, tbg_requested: bool):
    """Run the heavy processing pipeline in a thread."""
    with jobs_lock:
        jobs[job_id]["status"] = "processing"
    logger.info(f"Job {job_id} started processing")

    try:
        image = Image.open(BytesIO(raw)).convert("RGB")

        # Face detection BEFORE the heavy model
        face = detect_face(image)
        if face is None:
            with jobs_lock:
                jobs[job_id]["status"] = "failed"
                jobs[job_id]["error"] = (
                    "No face detected in the provided image. "
                    "Please upload a clear photo of a person."
                )
            logger.warning(f"Job {job_id} failed: no face detected")
            return

        cutout = remove_background(image)

        bg_w, bg_h = backgrounds[0].size
        portrait = build_portrait(cutout, face, bg_w, bg_h)

        # Save outputs
        first_last = name.replace(" ", "")
        email_slug = email.split("@")[0]
        user_dir = os.path.join(OUTPUT_DIR, email_slug)
        os.makedirs(user_dir, exist_ok=True)

        image_urls = []
        for i, bg in enumerate(backgrounds, start=1):
            result = composite_on_background(portrait, bg)
            filename = f"{first_last}-{i:02d}.png"
            filepath = os.path.join(user_dir, filename)
            result.convert("RGB").save(filepath, format="PNG")
            image_urls.append(f"/images/{email_slug}/{filename}")

        with jobs_lock:
            jobs[job_id]["image_urls"] = image_urls

        if tbg_requested:
            with jobs_lock:
                jobs[job_id]["tbg_status"] = "processing"

            try:
                profile = get_user_profile_by_email(email)
                tbg_urls = generate_teams_backgrounds(
                    email_slug=email_slug,
                    display_name=profile["display_name"],
                    job_title=profile["job_title"],
                    output_root=OUTPUT_DIR,
                )
                with jobs_lock:
                    jobs[job_id]["tbg_status"] = "done"
                    jobs[job_id]["tbg_image_urls"] = tbg_urls
                    jobs[job_id]["tbg_warning"] = None
                    jobs[job_id]["tbg_error"] = None
                    jobs[job_id]["status"] = "done"
            except (GraphError, TeamsBackgroundError) as e:
                with jobs_lock:
                    jobs[job_id]["tbg_status"] = "failed"
                    jobs[job_id]["tbg_error"] = str(e)
                    jobs[job_id]["status"] = "done_with_warnings"
                logger.warning(f"Job {job_id} Teams background generation failed: {e}")
            except Exception as e:
                with jobs_lock:
                    jobs[job_id]["tbg_status"] = "failed"
                    jobs[job_id]["tbg_error"] = f"Unexpected Teams background error: {e}"
                    jobs[job_id]["status"] = "done_with_warnings"
                logger.error(f"Job {job_id} unexpected Teams background failure: {e}")
        else:
            with jobs_lock:
                jobs[job_id]["status"] = "done"

        logger.info(f"Job {job_id} completed — {len(image_urls)} images")

    except Exception as e:
        with jobs_lock:
            jobs[job_id]["status"] = "failed"
            jobs[job_id]["error"] = str(e)
        logger.error(f"Job {job_id} failed: {e}")

    finally:
        # Force GC after each job — birefnet leaves large arrays in memory
        gc.collect()


# ── POST /process-image/ ────────────────────────────────────────────────────
@app.post("/process-image/", dependencies=[Depends(authorize)])
async def process_image(
    request: Request,
    file: UploadFile = File(None),
    image_url: str = Form(None),
    name: str = Form(...),
    email: str = Form(...),
    tbg: str = Form(None),
):
    request_id = request.state.request_id
    tbg_requested = parse_bool(tbg)

    # Rate limit
    if not check_rate_limit(email):
        raise HTTPException(
            status_code=429,
            detail="Rate limit exceeded. Max 5 requests/minute per email.",
        )

    # Validate input
    if not file and not image_url:
        return JSONResponse(
            status_code=400,
            content={
                "request_id": request_id,
                "message": "Provide either a file or image_url.",
            },
        )

    # Read image bytes
    if file:
        ext = os.path.splitext(file.filename)[1].lower()
        if ext not in ALLOWED_EXTENSIONS:
            return JSONResponse(
                status_code=400,
                content={
                    "request_id": request_id,
                    "message": "Invalid file type. Use JPG or PNG.",
                },
            )
        raw = await file.read()
        if len(raw) > MAX_FILE_SIZE_BYTES:
            return JSONResponse(
                status_code=400,
                content={
                    "request_id": request_id,
                    "message": "File too large. Max 10 MB.",
                },
            )
    else:
        encoded_url = urllib.parse.quote(image_url, safe=":/")
        stream = await download_image_from_url(encoded_url)
        raw = stream.read()

    # Create job
    job_id = str(uuid.uuid4())
    with jobs_lock:
        jobs[job_id] = {
            "status": "queued",
            "created_at": datetime.now(),
            "image_urls": None,
            "error": None,
            "tbg_requested": tbg_requested,
            "tbg_status": "not_requested" if not tbg_requested else "queued",
            "tbg_image_urls": None,
            "tbg_warning": None,
            "tbg_error": None,
        }

    logger.info(f"Job {job_id} created (email={email})")

    # Submit to thread pool
    executor.submit(_process_job, job_id, raw, name, email, tbg_requested)

    return JSONResponse(
        status_code=202,
        content={
            "request_id": request_id,
            "job_id": job_id,
            "status": "queued",
            "teams_backgrounds": {
                "requested": tbg_requested,
                "status": "not_requested" if not tbg_requested else "queued",
            },
        },
    )


# ── GET /status/{job_id} ────────────────────────────────────────────────────
@app.get("/status/{job_id}", dependencies=[Depends(authorize)])
async def get_job_status(job_id: str, request: Request):
    request_id = request.state.request_id

    with jobs_lock:
        job = jobs.get(job_id)

    if job is None:
        raise HTTPException(status_code=404, detail="Job not found or expired.")

    response = {
        "request_id": request_id,
        "job_id": job_id,
        "status": job["status"],
        "teams_backgrounds": {
            "requested": job.get("tbg_requested", False),
            "status": job.get("tbg_status", "not_requested"),
            "image_urls": job.get("tbg_image_urls"),
            "warning": job.get("tbg_warning"),
            "error": job.get("tbg_error"),
        },
    }
    if job["status"] in {"done", "done_with_warnings"}:
        response["image_urls"] = job["image_urls"]
    if job["status"] == "failed":
        response["error"] = job["error"]

    return JSONResponse(content=response)


# ── GET /backgrounds ─────────────────────────────────────────────────────────
@app.get("/backgrounds")
async def list_backgrounds(request: Request):
    request_id = request.state.request_id
    bg_names = [f"bg{i}" for i in range(1, len(backgrounds) + 1)]
    return JSONResponse(
        content={
            "request_id": request_id,
            "count": len(backgrounds),
            "backgrounds": bg_names,
        }
    )


# ── GET /health ──────────────────────────────────────────────────────────────
@app.get("/health")
async def health(request: Request):
    request_id = request.state.request_id
    with jobs_lock:
        job_count = len(jobs)
    return JSONResponse(
        content={
            "request_id": request_id,
            "status": "ok",
            "backgrounds_loaded": len(backgrounds),
            "model": "birefnet-portrait",
            "jobs_in_memory": job_count,
        }
    )


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8001)
