# company-bg

AI-powered employee photo background replacement service. Upload any photo of a person — the API detects their face, removes the background, and composites the result onto every PNG in the `backgrounds/` folder automatically.

Ships with branded compass-rose backgrounds. Add as many more PNGs as you want — no config changes needed.

## How It Works

1. **Face detection** — OpenCV locates the face (any orientation, any size). No face = instant fail, no wasted processing.
2. **Background removal** — rembg AI model cuts out the person cleanly (runs in an isolated subprocess for stability)
3. **Smart crop & scale** — face is centered on the background's compass center, person fills the frame
4. **Compositing** — one output PNG per background file
5. **Async processing** — job queued instantly; poll `/status/{job_id}` for results
6. **Auto-cleanup** — generated images and job records purged automatically

## API

### `POST /process-image/`

Requires Bearer token. Returns immediately with a `job_id`.

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `file` | File | One of these | Image upload (JPG/PNG, max 10MB) |
| `image_url` | string | One of these | URL to a publicly accessible image |
| `name` | string | ✅ | Person's full name |
| `email` | string | ✅ | Rate limit key + output folder |

```json
{ "job_id": "fa42c7b3-...", "status": "queued", "request_id": "a1b2c3" }
```

---

### `GET /status/{job_id}`

Requires Bearer token. Poll after submitting.

**Statuses:** `queued` → `processing` → `done` | `failed`

```json
{
  "job_id": "fa42c7b3-...",
  "status": "done",
  "image_urls": ["/images/jsmith/JohnSmith-01.png", "..."]
}
```

Images served at `http://<host>:8002/images/{email_slug}/{filename}` until cleanup runs.

---

### `GET /backgrounds`

Returns list of loaded backgrounds (no auth required).

```json
{ "count": 14, "backgrounds": ["bg1", "bg2", "..."] }
```

---

### `GET /health`

```json
{
  "status": "ok",
  "backgrounds_loaded": 14,
  "model": "birefnet-portrait",
  "jobs_in_memory": 2
}
```

---

## Setup

### Option A — Docker / Portainer (recommended)

```bash
git clone https://github.com/omichelbraga/company-bg.git
cd company-bg
cp .env.example .env
# Edit .env — set TOKEN at minimum
docker compose up -d
```

**Portainer Repository stack:**
- Repository URL: `https://github.com/omichelbraga/company-bg`
- Reference: `refs/heads/master`
- Compose path: `docker-compose.yml`
- Add env vars in the Portainer UI (TOKEN is required; rest have defaults)

First build takes ~5–10 min — both AI models are downloaded during build so runtime is instant.

---

### Option B — Local Python

```bash
git clone https://github.com/omichelbraga/company-bg.git
cd company-bg
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # set TOKEN
uvicorn microservice:app --host 0.0.0.0 --port 8000
```

---

### Backgrounds

Drop any PNG into the `backgrounds/` folder — picked up on next restart, sorted alphabetically, no limit.

---

## Configuration (`.env`)

| Variable | Default | Description |
|----------|---------|-------------|
| `TOKEN` | *(required)* | Bearer token for API auth |
| `REMBG_MODEL` | `birefnet-portrait` | AI model: `birefnet-portrait` (best quality) or `isnet-general-use` (faster, lighter) |
| `CLEANUP_AGE_MINUTES` | `5` | Age before output folders are deleted |
| `CLEANUP_INTERVAL_MINUTES` | `5` | How often cleanup runs |
| `RATE_LIMIT_MAX_REQUESTS` | `5` | Max requests per window per email |
| `RATE_LIMIT_WINDOW_MINUTES` | `1` | Rate limit window |
| `JOB_EXPIRY_MINUTES` | `10` | How long job records stay in memory |

Switching `REMBG_MODEL` only requires a container restart — no rebuild needed (both models are pre-downloaded in the image).

---

## Test

```bash
# Submit
curl -X POST http://localhost:8002/process-image/ \
  -H "Authorization: Bearer <TOKEN>" \
  -F "file=@photo.jpg" \
  -F "name=John Smith" \
  -F "email=john@example.com"

# Poll
curl http://localhost:8002/status/<job_id> \
  -H "Authorization: Bearer <TOKEN>"

# Download result
curl http://localhost:8002/images/john/JohnSmith-01.png -o result.png
```

A PowerShell script (`Submit-Photo.ps1`) is available separately — submits a photo and downloads all results to `Downloads\company-bg\`.

---

## Project Structure

```
company-bg/
├── microservice.py      # FastAPI app — auth, rate limiting, async jobs, scheduler
├── processor.py         # Image pipeline (face detection, compositing, alpha cleanup)
├── rembg_worker.py      # Isolated subprocess for AI background removal
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
- **rembg** — AI background removal (birefnet-portrait / isnet-general-use)
- **OpenCV** — face detection
- **Pillow** — image compositing and alpha processing
- **APScheduler** — periodic cleanup
- **python-dotenv** — config management
