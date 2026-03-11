"""
Photo Background Replacement Microservice
- POST /process-image/ (Bearer auth, rate-limited by email)
- Accepts: file upload OR image_url, plus name + email fields
- Returns: JSON with URLs to 14 generated images
- Images stored in out_images/{email_username}/ — auto-deleted after 5 min
"""

import os
import stat
import time
import shutil
import urllib.parse
from datetime import datetime, timedelta
from io import BytesIO

import requests as http_requests
from dotenv import load_dotenv
from fastapi import (
    BackgroundTasks, Depends, FastAPI, File, Form, HTTPException, UploadFile
)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from fastapi.staticfiles import StaticFiles

from processor import load_backgrounds, detect_face, remove_background, build_portrait, composite_on_background
from PIL import Image

load_dotenv()

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Config ────────────────────────────────────────────────────────────────────
BACKGROUND_DIR = "backgrounds/"
OUTPUT_DIR = "out_images/"
MAX_FILE_SIZE_BYTES = 10 * 1024 * 1024  # 10 MB
ALLOWED_EXTENSIONS = {".jpg", ".jpeg", ".png"}
CLEANUP_DELAY_SECONDS = 300  # 5 minutes

MAX_REQUESTS = 5
TIME_WINDOW = timedelta(minutes=1)

TOKEN = os.getenv("TOKEN")

# ── Startup ───────────────────────────────────────────────────────────────────
backgrounds: list = []

os.makedirs(OUTPUT_DIR, exist_ok=True)
app.mount("/images", StaticFiles(directory=OUTPUT_DIR), name="images")

@app.on_event("startup")
async def startup():
    global backgrounds
    backgrounds = load_backgrounds(BACKGROUND_DIR)
    print(f"Loaded {len(backgrounds)} backgrounds")

# ── Auth ──────────────────────────────────────────────────────────────────────
security = HTTPBearer()

def authorize(credentials: HTTPAuthorizationCredentials = Depends(security)):
    if credentials.credentials != TOKEN:
        raise HTTPException(status_code=401, detail="Unauthorized. Invalid token.")

# ── Rate limiting ─────────────────────────────────────────────────────────────
rate_limit_store: dict = {}

def check_rate_limit(email: str) -> bool:
    now = datetime.now()
    if email not in rate_limit_store:
        rate_limit_store[email] = []
    rate_limit_store[email] = [t for t in rate_limit_store[email] if now - t < TIME_WINDOW]
    if len(rate_limit_store[email]) >= MAX_REQUESTS:
        return False
    rate_limit_store[email].append(now)
    return True

# ── Cleanup ───────────────────────────────────────────────────────────────────
def delete_folder_after_delay(folder_path: str, delay: int = CLEANUP_DELAY_SECONDS):
    time.sleep(delay)
    try:
        if os.path.exists(folder_path):
            for root, dirs, files in os.walk(folder_path):
                for f in files:
                    os.chmod(os.path.join(root, f), stat.S_IWRITE)
            shutil.rmtree(folder_path)
            print(f"Cleaned up: {folder_path}")
    except Exception as e:
        print(f"Cleanup error: {e}")

# ── Helpers ───────────────────────────────────────────────────────────────────
async def download_image_from_url(image_url: str) -> BytesIO:
    headers = {"User-Agent": "Mozilla/5.0"}
    try:
        r = http_requests.get(image_url, headers=headers, timeout=15)
        r.raise_for_status()
        return BytesIO(r.content)
    except http_requests.RequestException as e:
        raise HTTPException(status_code=400, detail=f"Error downloading image: {str(e)}")

# ── Main endpoint ─────────────────────────────────────────────────────────────
@app.post("/process-image/", dependencies=[Depends(authorize)])
async def process_image(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(None),
    image_url: str = Form(None),
    name: str = Form(...),
    email: str = Form(...),
):
    # Rate limit
    if not check_rate_limit(email):
        raise HTTPException(status_code=429, detail="Rate limit exceeded. Max 5 requests/minute per email.")

    # Validate input
    if not file and not image_url:
        return JSONResponse(status_code=400, content={"message": "Provide either a file or image_url."})

    try:
        # ── Load image bytes ───────────────────────────────────────────────────
        if file:
            ext = os.path.splitext(file.filename)[1].lower()
            if ext not in ALLOWED_EXTENSIONS:
                return JSONResponse(status_code=400, content={"message": "Invalid file type. Use JPG or PNG."})
            raw = await file.read()
            if len(raw) > MAX_FILE_SIZE_BYTES:
                return JSONResponse(status_code=400, content={"message": f"File too large. Max 10 MB."})
        else:
            encoded_url = urllib.parse.quote(image_url, safe=":/")
            stream = await download_image_from_url(encoded_url)
            raw = stream.read()

        # ── Process using our pipeline ────────────────────────────────────────
        image = Image.open(BytesIO(raw)).convert("RGB")
        face = detect_face(image)
        cutout = remove_background(image)

        bg_w, bg_h = backgrounds[0].size
        portrait = build_portrait(cutout, face, bg_w, bg_h)

        # ── Save outputs ──────────────────────────────────────────────────────
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

        # Schedule cleanup after 5 minutes
        background_tasks.add_task(delete_folder_after_delay, user_dir, CLEANUP_DELAY_SECONDS)

        return JSONResponse(status_code=200, content={
            "message": "Images processed successfully",
            "image_urls": image_urls
        })

    except Exception as e:
        return JSONResponse(status_code=500, content={"message": f"Error: {str(e)}"})


@app.get("/health")
async def health():
    return {"status": "ok", "backgrounds_loaded": len(backgrounds)}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8001)
