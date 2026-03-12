# company-bg

AI-powered employee photo background replacement service. Upload any photo of a person — the API detects their face, removes the background, and composites the result onto 14 branded backgrounds automatically.

## How It Works

1. **Face detection** — OpenCV locates the face in the photo (any size, any orientation). If no face is found, the job fails immediately — no wasted processing.
2. **Background removal** — `birefnet-portrait` model cleanly cuts out the person
3. **Smart crop & scale** — face is centered on the background's compass center, person fills the frame
4. **Compositing** — outputs 14 branded background variants as PNG files
5. **Async processing** — job is queued instantly, processed in background; poll `/status/{job_id}` for results
6. **Auto-cleanup** — generated images and job records are purged every 5 minutes via APScheduler

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

**Rate limit:** 5 requests per minute per email address.

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
    "...",
    "/images/jsmith/JohnSmith-14.png"
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

Images are accessible at `http://<host>:8000/images/{email_slug}/{filename}` for 5 minutes after completion.

---

### `GET /backgrounds`

Returns available backgrounds (no auth required).

```json
{ "count": 14, "backgrounds": ["bg1", "bg2", ..., "bg14"] }
```

---

### `GET /health`

```json
{
  "status": "ok",
  "backgrounds_loaded": 14,
  "model": "birefnet-portrait",
  "jobs_in_memory": 3
}
```

## Setup

### Requirements
- Python 3.10+
- ~1.5GB disk for AI models (downloaded automatically on first run)

### Install

```bash
git clone https://github.com/omichelbraga/company-bg.git
cd company-bg
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

### Configure

```bash
cp .env.example .env
# Edit .env and set your TOKEN
```

### Add Backgrounds

Place your 14 background images in the `backgrounds/` folder named `bg1.png` through `bg14.png`.

### Run

```bash
uvicorn microservice:app --host 0.0.0.0 --port 8000
```

The `birefnet-portrait` model (~973MB) downloads automatically on first startup.

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

# 3. Access image
curl http://localhost:8000/images/john/JohnSmith-01.png
```

## Project Structure

```
company-bg/
├── microservice.py      # FastAPI app — async jobs, rate limiting, auth, scheduler
├── processor.py         # Image processing pipeline (face detection + birefnet)
├── backgrounds/         # bg1.png – bg14.png (not in repo)
├── out_images/          # Generated outputs (auto-cleaned every 5 min, not in repo)
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
