FROM python:3.12-slim

WORKDIR /app

COPY services/yp_gps/requirements.txt .
RUN pip install --no-cache-dir --retries 10 --timeout 120 -r requirements.txt

COPY yp_common ./yp_common
COPY services/yp_gps/yp_gps.py .

CMD ["python", "yp_gps.py"]
