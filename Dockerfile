FROM python:3.12-slim

# System deps for OpenCV + rembg
RUN apt-get update && apt-get install -y --no-install-recommends \
    libgl1 \
    libglib2.0-0 \
    libgomp1 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python dependencies first (layer cache)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY microservice.py processor.py ./

# Create output directory
RUN mkdir -p out_images

EXPOSE 8000

CMD ["uvicorn", "microservice:app", "--host", "0.0.0.0", "--port", "8000"]
