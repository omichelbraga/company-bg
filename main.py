"""FastAPI application for photo background replacement."""

from contextlib import asynccontextmanager

from fastapi import FastAPI, File, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, Response

from processor import load_backgrounds, process_photo

# Store backgrounds at module level, loaded during startup
backgrounds: list = []


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Load backgrounds at startup."""
    global backgrounds
    backgrounds = load_backgrounds("./backgrounds")
    print(f"Loaded {len(backgrounds)} background images")
    yield


app = FastAPI(title="Photo Background Tool", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
async def health():
    return {"status": "ok", "backgrounds_loaded": len(backgrounds)}


@app.get("/", response_class=HTMLResponse)
async def index():
    return """
<!DOCTYPE html>
<html>
<head>
    <title>Photo Background Tool</title>
    <style>
        body {
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
            max-width: 600px;
            margin: 60px auto;
            padding: 0 20px;
            background: #f5f5f5;
        }
        h1 { color: #333; }
        .upload-form {
            background: white;
            padding: 30px;
            border-radius: 8px;
            box-shadow: 0 2px 8px rgba(0,0,0,0.1);
        }
        input[type="file"] {
            margin: 15px 0;
            display: block;
        }
        button {
            background: #2563eb;
            color: white;
            border: none;
            padding: 10px 24px;
            border-radius: 6px;
            cursor: pointer;
            font-size: 16px;
        }
        button:hover { background: #1d4ed8; }
        button:disabled { background: #94a3b8; cursor: wait; }
        #status {
            margin-top: 15px;
            color: #666;
        }
    </style>
</head>
<body>
    <h1>Photo Background Tool</h1>
    <div class="upload-form">
        <p>Upload a photo of a person to generate portrait images with different backgrounds.</p>
        <form id="uploadForm">
            <input type="file" id="photo" name="photo" accept="image/*" required>
            <button type="submit" id="submitBtn">Process Photo</button>
        </form>
        <div id="status"></div>
    </div>
    <script>
        const form = document.getElementById('uploadForm');
        const status = document.getElementById('status');
        const submitBtn = document.getElementById('submitBtn');

        form.addEventListener('submit', async (e) => {
            e.preventDefault();
            const fileInput = document.getElementById('photo');
            if (!fileInput.files.length) return;

            const formData = new FormData();
            formData.append('photo', fileInput.files[0]);

            status.textContent = 'Processing... this may take a minute.';
            submitBtn.disabled = true;

            try {
                const response = await fetch('/process', {
                    method: 'POST',
                    body: formData
                });

                if (!response.ok) {
                    const err = await response.json();
                    throw new Error(err.detail || 'Processing failed');
                }

                const blob = await response.blob();
                const url = URL.createObjectURL(blob);
                const a = document.createElement('a');
                a.href = url;
                a.download = 'output_portraits.zip';
                a.click();
                URL.revokeObjectURL(url);
                status.textContent = 'Done! ZIP downloaded.';
            } catch (err) {
                status.textContent = 'Error: ' + err.message;
            } finally {
                submitBtn.disabled = false;
            }
        });
    </script>
</body>
</html>
"""


@app.post("/process")
async def process(photo: UploadFile = File(...)):
    """Process an uploaded photo: detect head+torso, remove background,
    composite onto all backgrounds, return as ZIP."""
    image_data = await photo.read()

    zip_bytes = process_photo(image_data, backgrounds)

    return Response(
        content=zip_bytes,
        media_type="application/zip",
        headers={
            "Content-Disposition": "attachment; filename=output_portraits.zip"
        },
    )


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
