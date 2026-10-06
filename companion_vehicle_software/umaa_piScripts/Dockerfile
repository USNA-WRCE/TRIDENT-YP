FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY --from=yp_common . ./yp_common
COPY umaa_bridge.py .
COPY umaa_bridge_wServer.py .

CMD ["python", "-u", "umaa_bridge.py"]