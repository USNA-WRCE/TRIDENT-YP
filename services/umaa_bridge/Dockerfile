FROM python:3.12-slim

WORKDIR /app

COPY services/umaa_bridge/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY yp_common ./yp_common
COPY services/umaa_bridge/umaa_bridge.py .

CMD ["python", "-u", "umaa_bridge.py"]