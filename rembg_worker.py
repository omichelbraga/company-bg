"""
Isolated subprocess worker for rembg inference.
Run as: python rembg_worker.py <input_png_path> <output_png_path>

Runs in its own process so a segfault in onnxruntime cannot kill uvicorn.
"""
import sys
from PIL import Image
from rembg import new_session, remove
import io

def main():
    if len(sys.argv) != 3:
        print("Usage: rembg_worker.py <input> <output>", file=sys.stderr)
        sys.exit(1)

    input_path = sys.argv[1]
    output_path = sys.argv[2]

    session = new_session("isnet-general-use")

    img = Image.open(input_path).convert("RGB")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    result_bytes = remove(buf.getvalue(), session=session)

    Image.open(io.BytesIO(result_bytes)).save(output_path, format="PNG")

if __name__ == "__main__":
    main()
