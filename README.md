# company-bg

AI-powered employee photo background replacement service. Upload any photo of a person — the API detects their face, removes the background, and composites the result onto every PNG in the `backgrounds/` folder. Ships with branded compass backgrounds; add as many more as you want.

## How It Works

1. **Face detection** — OpenCV locates the face in the photo (any size, any orientation). If no face is found, the job fails immediately — no wasted processing.
2. **Background removal** — `birefnet-portrait` model cleanly cuts out the person
3. **Smart crop & scale** — face is centered on the background's compass center, person fills the frame
4. **Compositing** — one output per background PNG in the `backgrounds/` folder
5. **Async processing** — job is queued instantly, processed in background; poll `/status/{job_id}` for results
6. **Auto-cleanup** — generated images and job records are purged automatically via APScheduler

## API

### `POST /process-image/`

Requires Bearer token. Returns immediately with a `job_id`.

**Form fields:**
| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `file` | File | One of these | Image upload (JPG/PNG, max 10MB) |
| `image_url` | string | One of these | URL to a publicly accessible image |
| `name` | string | ✅ | Person's full name (used for filenames) |
| `email` | string | ✅ | Used for rate limiting and output folder |

**Response `202 Accepted`:**
```json
{
  "request_id": "a1b2c3",
  "job_id": "fa42c7b3-cb8a-4e59-88be-212c09fb2811",
  "status": "queued"
}
```

**Rate limit:** Configurable via `.env` (default 5 requests per minute per email).

---

### `GET /status/{job_id}`

Requires Bearer token. Poll this after submitting a job.

**Statuses:** `queued` → `processing` → `done` | `failed`

**Response when done:**
```json
{
  "job_id": "fa42c7b3-...",
  "status": "done",
  "image_urls": [
    "/images/jsmith/JohnSmith-01.png",
    "/images/jsmith/JohnSmith-02.png",
    "..."
  ],
  "request_id": "a1b2c3"
}
```

**Response when failed:**
```json
{
  "job_id": "...",
  "status": "failed",
  "error": "No face detected in the provided image. Please upload a clear photo of a person.",
  "request_id": "..."
}
```

Images are accessible at `http://<host>:8000/images/{email_slug}/{filename}` until cleanup runs.

---

### `GET /backgrounds`

Returns list of loaded backgrounds (no auth required).

```json
{ "count": 8, "backgrounds": ["corporate-blue", "corporate-red", "..."] }
```

---

### `GET /health`

```json
{
  "status": "ok",
  "backgrounds_loaded": 8,
  "model": "birefnet-portrait",
  "jobs_in_memory": 2
}
```

## Setup

### Option A — Docker (recommended)

```bash
git clone https://github.com/omichelbraga/company-bg.git
cd company-bg

# Configure
cp .env.example .env
# Edit .env and set your TOKEN

# Backgrounds are already in the repo — add more PNGs to backgrounds/ anytime

# Build and run
docker compose up -d
```

The `birefnet-portrait` model (~973MB) downloads automatically on first startup and is cached in a named Docker volume (`model-cache`) — won't re-download on rebuild.

```bash
# Logs
docker compose logs -f

# Stop
docker compose down

# Rebuild after code changes
docker compose up -d --build
```

---

### Option B — Local (Python)

```bash
git clone https://github.com/omichelbraga/company-bg.git
cd company-bg
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # set your TOKEN
uvicorn microservice:app --host 0.0.0.0 --port 8000
```

### Backgrounds

Drop any PNG into the `backgrounds/` folder — picked up automatically on next restart, sorted alphabetically, no limit and no config changes needed.

## Configuration (`.env`)

```env
TOKEN=your-api-key                # Bearer token for auth
CLEANUP_AGE_MINUTES=5             # How old a folder must be before deletion
CLEANUP_INTERVAL_MINUTES=5        # How often the scheduler runs
RATE_LIMIT_MAX_REQUESTS=5         # Max requests per window per email
RATE_LIMIT_WINDOW_MINUTES=1       # Rate limit window duration
JOB_EXPIRY_MINUTES=10             # How long job records stay in memory
```

## Test

```bash
# 1. Submit job
curl -X POST http://localhost:8000/process-image/ \
  -H "Authorization: Bearer <your-token>" \
  -F "file=@photo.jpg" \
  -F "name=John Smith" \
  -F "email=john@example.com"

# 2. Poll status
curl http://localhost:8000/status/<job_id> \
  -H "Authorization: Bearer <your-token>"

# 3. Access image directly
curl http://localhost:8000/images/john/JohnSmith-01.png
```

## Project Structure

```
company-bg/
├── microservice.py      # FastAPI app — async jobs, rate limiting, auth, scheduler
├── processor.py         # Image processing pipeline (face detection + birefnet)
├── backgrounds/         # PNG backgrounds — add as many as you want
├── out_images/          # Generated outputs (auto-cleaned, not in repo)
├── Dockerfile
├── docker-compose.yml
├── requirements.txt
├── .env.example
└── README.md
```

## Stack

- **FastAPI** — API framework
- **rembg + birefnet-portrait** — state-of-the-art portrait background removal
- **OpenCV** — face detection
- **Pillow** — image compositing
- **APScheduler** — periodic cleanup of output files and expired jobs
- **python-dotenv** — config management
