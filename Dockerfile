FROM python:3.12-slim

# System deps for OpenCV + rembg
RUN apt-get update && apt-get install -y --no-install-recommends \
    libcairo2 \
    libffi-dev \
    libgdk-pixbuf-2.0-0 \
    libgl1 \
    libglib2.0-0 \
    libgomp1 \
    libpango-1.0-0 \
    libpangocairo-1.0-0 \
    shared-mime-info \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python dependencies first (layer cache)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code and backgrounds
COPY microservice.py processor.py rembg_worker.py graph_client.py tbg_processor.py ./
COPY backgrounds/ ./backgrounds/
COPY tbg/ ./tbg/

# Pre-download the birefnet-portrait model at build time (~973MB)
# Avoids first-request download race condition in production
# Pre-download both models so switching via REMBG_MODEL env var needs no rebuild
RUN python3 -c "from rembg import new_session; new_session('birefnet-portrait', providers=['CPUExecutionProvider']); print('birefnet ready')"
RUN python3 -c "from rembg import new_session; new_session('isnet-general-use'); print('isnet ready')"

# Create output directory
RUN mkdir -p out_images

EXPOSE 8000

CMD ["uvicorn", "microservice:app", "--host", "0.0.0.0", "--port", "8000"]
