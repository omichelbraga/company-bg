"""Image processing logic for photo background replacement."""

import io
import zipfile
from pathlib import Path
from typing import Optional

import cv2
import numpy as np
from PIL import Image
from rembg import new_session, remove

# Load the portrait-optimized model once at startup (973MB, don't reload per request)
_REMBG_SESSION = new_session("birefnet-portrait")


def load_backgrounds(bg_dir: str = "./backgrounds") -> list[Image.Image]:
    """Load all background images from the backgrounds directory at startup."""
    bg_path = Path(bg_dir)
    backgrounds: list[Image.Image] = []
    for i in range(1, 15):
        filepath = bg_path / f"bg{i}.png"
        if filepath.exists():
            bg = Image.open(filepath).convert("RGBA")
            backgrounds.append(bg)
    return backgrounds


def detect_face(image: Image.Image) -> Optional[tuple[int, int, int, int]]:
    """Detect the largest face. Returns (x, y, w, h) or None.

    Uses strict thresholds to avoid false positives (fingers, objects, textures).
    A valid face must:
    - Pass with minNeighbors=10 (high confidence, reduces false positives significantly)
    - Be at least 8% of the image width AND 8% of the image height
    - Be at least 60x60 pixels in absolute size
    """
    img_array = np.array(image.convert("RGB"))
    h, w = img_array.shape[:2]
    gray = cv2.cvtColor(img_array, cv2.COLOR_RGB2GRAY)

    # Minimum face size: 8% of image dimensions, at least 60px
    min_face_px = max(60, int(min(w, h) * 0.08))

    cascade_path = cv2.data.haarcascades + "haarcascade_frontalface_default.xml"
    faces = cv2.CascadeClassifier(cascade_path).detectMultiScale(
        gray, scaleFactor=1.1, minNeighbors=10, minSize=(min_face_px, min_face_px)
    )

    if len(faces) == 0:
        profile_path = cv2.data.haarcascades + "haarcascade_profileface.xml"
        faces = cv2.CascadeClassifier(profile_path).detectMultiScale(
            gray, scaleFactor=1.1, minNeighbors=10, minSize=(min_face_px, min_face_px)
        )

    if len(faces) == 0:
        return None

    # Pick the largest detection and validate it's a reasonable size
    faces = sorted(faces, key=lambda f: f[2] * f[3], reverse=True)
    fx, fy, fw, fh = faces[0]

    # Reject if face is smaller than 8% of image width or height (likely a false positive)
    if fw < w * 0.08 or fh < h * 0.08:
        return None

    return (fx, fy, fw, fh)


def remove_background(image: Image.Image) -> Image.Image:
    """Remove background from the full image using rembg."""
    img_bytes = io.BytesIO()
    image.save(img_bytes, format="PNG")
    img_bytes.seek(0)
    result_bytes = remove(img_bytes.read(), session=_REMBG_SESSION)
    cutout = Image.open(io.BytesIO(result_bytes)).convert("RGBA")

    # Clean up semi-transparent background artifacts
    alpha = np.array(cutout.split()[3], dtype=np.uint8)
    alpha[alpha < 30] = 0   # kill clear background pixels

    # Erode the alpha mask to remove thin edge artifacts (dark slivers, shoulder fringing)
    kernel = np.ones((3, 3), np.uint8)
    alpha = cv2.erode(alpha, kernel, iterations=2)

    # Smooth the edges with a slight Gaussian blur on the alpha channel
    alpha_smooth = cv2.GaussianBlur(alpha, (5, 5), sigmaX=1.5)

    # Re-apply the hard floor after smoothing (don't let blur resurrect background)
    alpha_smooth[alpha == 0] = 0

    cutout.putalpha(Image.fromarray(alpha_smooth, mode="L"))
    return cutout


def build_portrait(
    cutout: Image.Image,
    face: Optional[tuple[int, int, int, int]],
    bg_w: int,
    bg_h: int,
) -> Image.Image:
    """Scale and crop cutout to bg dimensions with face centered on compass center.

    Strategy:
    - Scale the cutout so the face height = ~38% of bg height (fills frame nicely)
    - Crop to bg dimensions, face center aligned to bg center
    - If person extends beyond edges, they get clipped naturally (no floating box)
    """
    orig_w, orig_h = cutout.size

    if face is not None:
        fx, fy, fw, fh = face
        face_center_x = fx + fw / 2
        face_center_y = fy + fh / 2

        # Scale so face height = 38% of background height
        target_face_h = bg_h * 0.38
        scale = target_face_h / fh
    else:
        # No face detected: scale so full image fits bg height, center it
        face_center_x = orig_w / 2
        face_center_y = orig_h * 0.30
        scale = bg_h / orig_h

    new_w = int(orig_w * scale)
    new_h = int(orig_h * scale)

    # Ensure the scaled person fills the entire background (cover fit — no black bars).
    # If the scaled image is narrower or shorter than the bg, scale up to cover.
    fill_scale = max(bg_w / new_w, bg_h / new_h)
    if fill_scale > 1.0:
        scale *= fill_scale
        new_w = int(orig_w * scale)
        new_h = int(orig_h * scale)

    scaled = cutout.resize((new_w, new_h), Image.Resampling.LANCZOS)

    # Face center position in scaled image
    sc_face_x = face_center_x * scale
    sc_face_y = face_center_y * scale

    # Crop window: face center → bg center
    crop_left = int(sc_face_x - bg_w / 2)
    crop_top = int(sc_face_y - bg_h / 2)
    crop_right = crop_left + bg_w
    crop_bottom = crop_top + bg_h

    # Paste scaled cutout onto a transparent canvas of bg dimensions
    canvas = Image.new("RGBA", (bg_w, bg_h), (0, 0, 0, 0))

    # Source region (clamped to scaled image bounds)
    src_left = max(0, crop_left)
    src_top = max(0, crop_top)
    src_right = min(new_w, crop_right)
    src_bottom = min(new_h, crop_bottom)

    # Destination offset on canvas
    dst_x = src_left - crop_left
    dst_y = src_top - crop_top

    region = scaled.crop((src_left, src_top, src_right, src_bottom))
    canvas.paste(region, (dst_x, dst_y), region)

    return canvas


def composite_on_background(portrait: Image.Image, background: Image.Image) -> Image.Image:
    """Paste the portrait cutout (bg-sized canvas) onto the background."""
    result = background.copy()
    result.paste(portrait, (0, 0), portrait)
    return result


def _render_all(image_data: bytes, backgrounds: list[Image.Image]) -> list[Image.Image]:
    """Shared core: detect, remove bg, build portrait, composite. Returns list of result images."""
    image = Image.open(io.BytesIO(image_data)).convert("RGB")
    face = detect_face(image)
    cutout = remove_background(image)
    bg_w, bg_h = backgrounds[0].size
    portrait = build_portrait(cutout, face, bg_w, bg_h)
    return [composite_on_background(portrait, bg) for bg in backgrounds]


def process_photo(image_data: bytes, backgrounds: list[Image.Image]) -> bytes:
    """Full pipeline → returns ZIP of all 14 PNGs."""
    results = _render_all(image_data, backgrounds)
    zip_buffer = io.BytesIO()
    with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        for i, result in enumerate(results, start=1):
            img_buffer = io.BytesIO()
            result.convert("RGB").save(img_buffer, format="PNG")
            img_buffer.seek(0)
            zf.writestr(f"portrait_bg{i:02d}.png", img_buffer.getvalue())
    zip_buffer.seek(0)
    return zip_buffer.getvalue()


def process_photo_json(image_data: bytes, backgrounds: list[Image.Image]) -> dict:
    """Full pipeline → returns JSON with all 14 images as base64 PNG strings."""
    import base64
    results = _render_all(image_data, backgrounds)
    images = []
    for i, result in enumerate(results, start=1):
        img_buffer = io.BytesIO()
        result.convert("RGB").save(img_buffer, format="PNG")
        b64 = base64.b64encode(img_buffer.getvalue()).decode("utf-8")
        images.append({
            "index": i,
            "filename": f"portrait_bg{i:02d}.png",
            "data": f"data:image/png;base64,{b64}"
        })
    return {"count": len(images), "images": images}
