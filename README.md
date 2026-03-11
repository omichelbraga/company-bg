# company-bg

AI-powered employee photo background replacement service. Upload any photo of a person — the API detects their face, removes the background, and composites the result onto 14 branded backgrounds automatically.

## How It Works

1. **Face detection** — OpenCV locates the face in the photo (any size, any orientation)
2. **Background removal** — `birefnet-portrait` model cleanly cuts out the person
3. **Smart crop & scale** — face is centered on the background's compass center, person fills the frame
4. **Compositing** — outputs 14 branded background variants as PNG files
5. **Auto-cleanup** — generated images are deleted after 5 minutes

## API

### `POST /process-image/`

Requires Bearer token authentication.

**Form fields:**
| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `file` | File | One of these | Image upload (JPG/PNG, max 10MB) |
| `image_url` | string | One of these | URL to a publicly accessible image |
| `name` | string | ✅ | Person's full name (used for filenames) |
| `email` | string | ✅ | Used for rate limiting and output folder |

**Response:**
```json
{
  "message": "Images processed successfully",
  "image_urls": [
    "/images/jsmith/JohnSmith-01.png",
    "/images/jsmith/JohnSmith-02.png",
    "...",
    "/images/jsmith/JohnSmith-14.png"
  ]
}
```

Images are accessible at `http://<host>:8000/images/{email_slug}/{filename}` for 5 minutes.

**Rate limit:** 5 requests per minute per email address.

### `GET /health`
```json
{ "status": "ok", "backgrounds_loaded": 14 }
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

The birefnet-portrait model (~973MB) will download automatically on first startup.

## Test

```bash
curl -X POST http://localhost:8000/process-image/ \
  -H "Authorization: Bearer <your-token>" \
  -F "file=@photo.jpg" \
  -F "name=John Smith" \
  -F "email=john@example.com"
```

## Project Structure

```
company-bg/
├── microservice.py      # FastAPI app (main entry point)
├── processor.py         # Image processing pipeline
├── backgrounds/         # bg1.png – bg14.png (not in repo)
├── out_images/          # Generated outputs (auto-cleaned, not in repo)
├── requirements.txt
├── .env.example
└── README.md
```

## Stack

- **FastAPI** — API framework
- **rembg + birefnet-portrait** — state-of-the-art portrait background removal
- **OpenCV** — face detection
- **Pillow** — image compositing
- **python-dotenv** — config management
