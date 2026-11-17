# Simulated boat telemetry example

This standalone example publishes telemetry directly to the TRIDENT server. It does not use the command client or control a real vehicle. The boat appears in the GCS and travels around a circle with a 10-meter radius until you stop the script with **Ctrl+C**. Disconnecting removes it from the live vehicle display.

## Run from VS Code or Thonny

Open [`boat_circle_ide.py`](boat_circle_ide.py), edit the configuration values near the top (`VEHICLE_NAME`, `VEHICLE_TYPE`, `BASE_LATITUDE`, `BASE_LONGITUDE`, and `SERVER_URL`), then run the file using the IDE's Run button. Stop it with **Ctrl+C** in the IDE's terminal/shell. If `websockets` is missing, install it in the Python environment selected by the IDE. In Thonny, use **Tools > Manage packages** to find and install `websockets`; in VS Code, select the intended Python interpreter, then install it from that interpreter's terminal:

```bash
python -m pip install websockets
```

## Run with command-line options

The original `boat_circle.py` example also supports command-line options. Install the dependency:

```bash
python -m pip install -r requirements.txt
```

With the server reachable at `ws://localhost:8000`, run:

```bash
python boat_circle.py
```

The default vehicle ID is `boat-student-01` and the circle is centered at latitude `38.9822`, longitude `-76.4819`. Set a different ID and center for your server/map:

```bash
python boat_circle.py --vehicle-id student-boat --url ws://yp-server:8000/ws/vehicle \
  --latitude 38.9822 --longitude -76.4819
```

The script sends JSON telemetry messages to `/ws/vehicle/{vehicle_id}` five times per second. It sends a simulated `usv` position, heading, and status; it does not receive or execute vehicle commands. Do not treat this example as a real vessel connection or run it with an ID that belongs to a real vehicle.
