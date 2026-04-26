# company-bg

AI-powered employee photo background replacement service. Upload any photo of a person — the API detects their face, removes the background, and composites the result onto every PNG in the `backgrounds/` folder automatically. Optionally also generates Microsoft Teams meeting backgrounds populated with the user's display name and job title (pulled from Microsoft Entra ID via Graph).

Ships with branded compass-rose backgrounds. Add as many more PNGs as you want — no config changes needed.

## How It Works

1. **Face detection** — OpenCV locates the face (any orientation, any size). No face = instant fail, no wasted processing.
2. **Background removal** — rembg AI model cuts out the person cleanly (runs in an isolated subprocess for stability)
3. **Smart crop & scale** — face is centered on the background's compass center, person fills the frame
4. **Compositing** — one output PNG per background file
5. **Async processing** — job queued instantly; poll `/status/{job_id}` for results
6. **Optional: Teams backgrounds** — when `tbg=true`, after the photo composites finish, the service looks up the user in Microsoft Entra (by `mail` then `userPrincipalName`), populates 8 SVG templates with their display name + job title, and renders them to PNG.
7. **Auto-cleanup** — generated images and job records purged automatically

## API

### `POST /process-image/`

Requires Bearer token. Returns immediately with a `job_id`.

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `file` | File | One of these | Image upload (JPG/PNG, max 10 MB) |
| `image_url` | string | One of these | URL to a publicly accessible image |
| `name` | string | ✅ | Person's full name |
| `email` | string | ✅ | Rate limit key + output folder + Entra lookup key |
| `tbg` | string | optional | `true` / `1` / `yes` / `on` to also generate Teams backgrounds |

```json
{
  "request_id": "a1b2c3",
  "job_id": "fa42c7b3-...",
  "status": "queued",
  "teams_backgrounds": { "requested": true, "status": "queued" }
}
```

---

### `GET /status/{job_id}`

Requires Bearer token. Poll after submitting.

**Statuses:**
- Photos: `queued` → `processing` → `done` | `done_with_warnings` | `failed`
- Teams BG: `not_requested` | `queued` | `processing` | `done` | `failed`

`done_with_warnings` means photos succeeded but Teams BG failed (e.g., Graph user not found, Graph misconfigured). Photo image URLs are still returned.

```json
{
  "request_id": "a1b2c3",
  "job_id": "fa42c7b3-...",
  "status": "done",
  "image_urls": ["/images/jsmith/JohnSmith-01.png", "..."],
  "teams_backgrounds": {
    "requested": true,
    "status": "done",
    "image_urls": ["/images/jsmith/teams-backgrounds/tbg1.png", "..."],
    "warning": null,
    "error": null
  }
}
```

Images served at `http://<host>:8002/images/{email_slug}/{filename}` until cleanup runs.

---

### `GET /backgrounds`

Returns list of loaded backgrounds (no auth required).

```json
{ "request_id": "a1b2c3", "count": 37, "backgrounds": ["bg1", "bg2", "..."] }
```

---

### `GET /health`

```json
{
  "request_id": "a1b2c3",
  "status": "ok",
  "backgrounds_loaded": 37,
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
# Edit .env — set TOKEN at minimum (and GRAPH_* if you want Teams backgrounds)
docker compose up -d
```

**Portainer Repository stack (production):**
- Repository URL: `https://github.com/omichelbraga/company-bg`
- Reference: `refs/heads/master`
- Compose path: `docker-compose.yml`
- Add env vars in the Portainer UI (TOKEN required; GRAPH_* required only for Teams BG; rest have defaults)

First build takes ~5–10 min — both rembg AI models (birefnet-portrait ~973 MB and isnet-general-use ~170 MB) are downloaded during build so runtime is instant and switching models needs no rebuild.

After initial setup, every code change ships with a single git redeploy:

```
PUT /api/stacks/{id}/git/redeploy?endpointId={endpointId}
{
  "RepositoryReferenceName": "refs/heads/master",
  "RepositoryAuthentication": false,
  "PullImage": true,
  "Prune": false
}
```

> Note: an upstream reverse proxy may return HTTP 504 on long builds — the build still completes server-side. Poll `GET /api/stacks/{id}` and watch `GitConfig.ConfigHash` update to the new commit SHA.

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

### Teams Backgrounds (optional)

8 SVG templates live in `tbg/`. Each contains `{{DisplayName}}` and `{{JobTitle}}` placeholders that are replaced at render time with values from Microsoft Entra. Render is via `cairosvg` to PNG.

To add a new Teams template: drop an SVG into `tbg/` containing those placeholders.

The Microsoft Graph app registration needs the application permission **`User.Read.All`** (admin-consented) so it can resolve users by email.

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
| `GRAPH_TENANT_ID` | *(empty)* | Entra tenant ID — required for Teams BG |
| `GRAPH_CLIENT_ID` | *(empty)* | App registration client ID — required for Teams BG |
| `GRAPH_CLIENT_SECRET` | *(empty)* | App registration client secret — required for Teams BG |
| `GRAPH_TIMEOUT_SECONDS` | `15` | HTTP timeout for Graph requests |

Switching `REMBG_MODEL` only requires a container restart — no rebuild needed (both models are pre-downloaded in the image).

Graph access tokens are cached in-memory with a TTL slightly shorter than Microsoft's stated `expires_in`, and any 401 from Graph triggers a one-shot forced refresh + retry — see `graph_client.py`.

---

## Test

```bash
# Submit a photo (no Teams BG)
curl -X POST http://localhost:8002/process-image/ \
  -H "Authorization: Bearer <TOKEN>" \
  -F "file=@photo.jpg" \
  -F "name=John Smith" \
  -F "email=john@example.com"

# Submit + generate Teams backgrounds
curl -X POST http://localhost:8002/process-image/ \
  -H "Authorization: Bearer <TOKEN>" \
  -F "file=@photo.jpg" \
  -F "name=John Smith" \
  -F "email=john@example.com" \
  -F "tbg=true"

# Poll
curl http://localhost:8002/status/<job_id> \
  -H "Authorization: Bearer <TOKEN>"

# Download a result
curl http://localhost:8002/images/john/JohnSmith-01.png -o result.png
```

A PowerShell script (`Submit-Photo.ps1`) is available separately — submits a photo and downloads all results to `Downloads\company-bg\`.

### Unit tests

```bash
pip install pytest
pytest tests/
```

Covers the Microsoft Graph token cache (TTL-aware, force-refresh, 401-retry, and a regression guard against re-adding `@lru_cache` to `get_access_token`).

---

## Project Structure

```
company-bg/
├── microservice.py      # FastAPI app — auth, rate limiting, async jobs, scheduler
├── processor.py         # Image pipeline (face detection, compositing, alpha cleanup)
├── rembg_worker.py      # Isolated subprocess for AI background removal
├── graph_client.py      # Microsoft Graph client (TTL-cached token + 401 retry)
├── tbg_processor.py     # Teams background SVG templating + render
├── backgrounds/         # PNG backgrounds — add as many as you want
├── tbg/                 # Teams background SVG templates
├── tests/               # pytest suite (Graph client behavior)
├── out_images/          # Generated outputs (auto-cleaned, not in repo)
├── docs/                # Design docs (TECHNICAL_PLAN_TBG_ENTRA.md)
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
- **cairosvg** — SVG → PNG for Teams backgrounds
- **requests** — Microsoft Graph HTTP client
- **python-dotenv** — config management
