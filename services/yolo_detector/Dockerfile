FROM python:3.12-slim

# libGL/libglib are required by opencv-python-headless's video decoding backend
RUN apt-get update && apt-get install -y --no-install-recommends \
    libgl1 libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# GPU backend selection for torch/torchvision, chosen at build time so the same
# Dockerfile serves AMD (ROCm), NVIDIA (CUDA), and CPU-only hosts:
#   - AMD/ROCm (default, matches the current test machine): leave as-is.
#   - NVIDIA: override with `--build-arg TORCH_INDEX_URL=` (empty) or a pinned
#     CUDA index (e.g. https://download.pytorch.org/whl/cu124); standard PyPI
#     torch/torchvision wheels on Linux already bundle CUDA support.
#   - CPU-only: `--build-arg TORCH_INDEX_URL=https://download.pytorch.org/whl/cpu`.
# Falls back to CPU automatically at runtime if no GPU is exposed to the container.
ARG TORCH_INDEX_URL=https://download.pytorch.org/whl/rocm6.2
RUN if [ -n "$TORCH_INDEX_URL" ]; then \
      pip install --no-cache-dir --retries 10 --timeout 120 torch torchvision --index-url "$TORCH_INDEX_URL"; \
    else \
      pip install --no-cache-dir --retries 10 --timeout 120 torch torchvision; \
    fi

COPY requirements.txt .
RUN pip install --no-cache-dir --retries 10 --timeout 120 -r requirements.txt

# Bake the default model into the image so first boot works without outbound
# internet access; it's copied into the shared models volume at runtime.
RUN python -c "from ultralytics.utils.downloads import attempt_download_asset; attempt_download_asset('yolov8n.pt', dir='/app')"

COPY yolo_detector.py .

CMD ["python", "yolo_detector.py"]
