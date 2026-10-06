FROM python:3.12-slim

WORKDIR /app

COPY services/sim_vehicle/requirements.txt .
RUN pip install --no-cache-dir --retries 10 --timeout 120 -r requirements.txt

COPY yp_common ./yp_common
COPY services/sim_vehicle/sim_vehicle.py .

CMD ["python", "sim_vehicle.py"]
