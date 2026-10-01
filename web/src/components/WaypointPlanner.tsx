import {
  useRef,
  useState,
  type ChangeEvent,
  type PointerEvent as ReactPointerEvent,
} from "react";
import { Circle, Download, Trash2, Upload } from "lucide-react";
import { WaypointScene } from "./3d/WaypointScene";
import type { Command, RelativeWaypoint, Vehicle } from "../types";
import { applyDispatchAltitudeOffset, parseLocalWaypointPlan, serializeLocalWaypointPlan } from "../services/localWaypointPlan";
import { generateCircularWaypoints, yawTowardOrigin } from "../services/circularWaypoints";

type LocalWaypoint = { id: string; x: number; y: number; z: number; yaw: number };

export function WaypointPlanner({
  yp,
  vehicles,
  onCommand,
}: {
  yp?: Vehicle;
  vehicles: Vehicle[];
  onCommand: (vehicleId: string, cmd: Command) => void;
}) {
  const [waypoints, setWaypoints] = useState<LocalWaypoint[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [selectedVehicleId, setSelectedVehicleId] = useState("");
  const [faceInward, setFaceInward] = useState(false);
  const [holdLastWaypoint, setHoldLastWaypoint] = useState(false);
  const [missionLoops, setMissionLoops] = useState(1);
  const [dispatchAltitudeOffset, setDispatchAltitudeOffset] = useState(15);
  const [circleRadius, setCircleRadius] = useState(30);
  const [circleWaypointCount, setCircleWaypointCount] = useState(8);
  const previewWaypoints = faceInward
    ? waypoints.map((waypoint) => ({ ...waypoint, yaw: yawTowardOrigin(waypoint.x, waypoint.y) }))
    : waypoints;
  const importFileRef = useRef<HTMLInputElement>(null);
  const updateWaypoint = (id: string, updates: Partial<LocalWaypoint>) =>
    setWaypoints((items) =>
      items.map((item) => (item.id === id ? { ...item, ...updates } : item)),
    );
  const deleteWaypoint = (id: string) => {
    setWaypoints((items) => items.filter((item) => item.id !== id));
    if (selectedId === id) setSelectedId(null);
  };
  const generateCircle = () => {
    const generated = generateCircularWaypoints(circleRadius, circleWaypointCount);
    setWaypoints(generated.map((waypoint, index) => ({
      id: `${Date.now()}-${index}`,
      x: waypoint.x,
      y: waypoint.y,
      z: waypoint.z,
      yaw: waypoint.yaw_deg,
    })));
    setSelectedId(null);
  };
  const exportPlan = () => {
    if (!waypoints.length) {
      alert("Add at least one waypoint before exporting a plan.");
      return;
    }
    const content = serializeLocalWaypointPlan(
      waypoints.map(({ x, y, z, yaw }) => ({ x, y, z, yaw_deg: yaw })),
      faceInward,
      missionLoops,
      dispatchAltitudeOffset,
      holdLastWaypoint,
    );
    const url = URL.createObjectURL(new Blob([content], { type: "application/json" }));
    const link = document.createElement("a");
    link.href = url;
    link.download = `local-waypoint-plan-${new Date().toISOString().replace(/[:.]/g, "-")}.json`;
    document.body.appendChild(link);
    link.click();
    link.remove();
    URL.revokeObjectURL(url);
  };
  const importPlan = async (event: ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0];
    if (!file) return;
    try {
      const plan = parseLocalWaypointPlan(await file.text());
      setWaypoints(plan.waypoints.map(({ x, y, z, yaw_deg }, index) => ({
        id: `${Date.now()}-${index}`, x, y, z, yaw: yaw_deg,
      })));
      setSelectedId(null);
      if (plan.faceShip != null) setFaceInward(plan.faceShip);
      if (plan.holdLastWaypoint != null) setHoldLastWaypoint(plan.holdLastWaypoint);
      if (plan.loopCount != null) setMissionLoops(plan.loopCount);
      if (plan.dispatchAltitudeOffset != null) setDispatchAltitudeOffset(plan.dispatchAltitudeOffset);
    } catch (error) {
      alert(error instanceof Error ? error.message : "Failed to import local waypoint plan.");
    } finally {
      event.target.value = "";
    }
  };
  const dispatch = () => {
    if (!yp?.position || yp.heading == null) {
      alert("Cannot dispatch: YP GPS or heading is unavailable.");
      return;
    }
    if (!waypoints.length) {
      alert("Add at least one waypoint before dispatching.");
      return;
    }
    if (!selectedVehicleId) {
      alert("Please select a vehicle to dispatch.");
      return;
    }
    const altitudeAdjustedWaypoints = applyDispatchAltitudeOffset(
      waypoints.map(({ x, y, z, yaw }) => ({ x, y, z, yaw_deg: yaw })),
      dispatchAltitudeOffset,
    );
    const localWaypoints: RelativeWaypoint[] = altitudeAdjustedWaypoints;
    onCommand(selectedVehicleId, {
      type: "ship_relative_trajectory",
      ship_vehicle_id: yp.vehicle_id,
      local_waypoints: localWaypoints,
      arrival_radius_m: 6,
      update_hz: 10,
      face_ship: faceInward,
      loop_count: missionLoops,
      hold_last_waypoint: holdLastWaypoint,
    });
    alert(
      `Dispatched ${localWaypoints.length} waypoints for ${missionLoops} loop${missionLoops === 1 ? "" : "s"} to ${selectedVehicleId}${holdLastWaypoint ? ". Holding final relative waypoint until retasked" : ""}`,
    );
    setWaypoints([]);
    setSelectedId(null);
  };
  return (
    <div
      className="planner-container"
      style={{
        display: "flex",
        flexDirection: "column",
        width: "100%",
        height: "100%",
        overflow: "hidden",
        backgroundColor: "#0f172a",
        color: "white",
        paddingTop: 60,
      }}
    >
      <div
        style={{
          flex: "2 1 0",
          minHeight: 0,
          position: "relative",
          borderBottom: "2px solid #334155",
          overflow: "hidden",
        }}
      >
        <WaypointScene waypoints={previewWaypoints} selectedId={selectedId} />
      </div>
      <div
        style={{
          flex: "3 1 0",
          minHeight: 0,
          display: "flex",
          overflow: "hidden",
        }}
      >
        <div
          style={{
            flex: "1 1 0",
            minWidth: 0,
            padding: 20,
            borderRight: "2px solid #334155",
            display: "flex",
            flexDirection: "column",
            overflow: "hidden",
          }}
        >
          <div
            style={{
              display: "flex",
              justifyContent: "space-between",
              marginBottom: 10,
            }}
          >
            <h2 style={{ fontSize: "1.2rem", fontWeight: "bold" }}>
              Lateral Planner (Top-Down)
            </h2>
            <button
              onClick={() => selectedId && deleteWaypoint(selectedId)}
              disabled={!selectedId}
              style={{
                padding: "4px 8px",
                background: "#ef4444",
                color: "white",
                border: "none",
                borderRadius: 4,
                opacity: selectedId ? 1 : 0,
                pointerEvents: selectedId ? "auto" : "none",
              }}
            >
              <Trash2 size={14} /> Delete Selected
            </button>
          </div>
          <div
            style={{
              flex: 1,
              minHeight: 0,
              display: "flex",
              justifyContent: "center",
              alignItems: "center",
              overflow: "hidden",
            }}
          >
            <InteractiveWaypoint2D
              waypoints={previewWaypoints}
              selectedId={selectedId}
              orientationForced={faceInward}
              onSelect={setSelectedId}
              onAdd={(x, y) => {
                const id = Date.now().toString();
                setWaypoints((items) => [...items, { id, x, y, z: 0, yaw: 0 }]);
                setSelectedId(id);
              }}
              onUpdate={updateWaypoint}
            />
          </div>
        </div>
        <div
          style={{
            flex: "1 1 0",
            minWidth: 0,
            padding: 20,
            display: "flex",
            flexDirection: "column",
            overflow: "hidden",
          }}
        >
          <h2
            style={{ fontSize: "1.2rem", fontWeight: "bold", marginBottom: 10 }}
          >
            Altitude Profile (m above YP)
          </h2>
          <div
            style={{
              flex: 1,
              minHeight: 0,
              backgroundColor: "#1e293b",
              borderRadius: 8,
              border: "1px solid #475569",
              position: "relative",
              marginBottom: 15,
              padding: "10px 0",
              overflow: "hidden",
            }}
          >
            <AltitudeProfile
              waypoints={waypoints}
              selectedId={selectedId}
              dispatchAltitudeOffset={dispatchAltitudeOffset}
              onSelect={setSelectedId}
              onUpdateAltitude={(id, altitude) => updateWaypoint(id, { z: Math.max(-dispatchAltitudeOffset, altitude - dispatchAltitudeOffset) })}
            />
          </div>
          <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 10, marginBottom: 10, flexWrap: "wrap" }}>
            <div style={{ display: "flex", alignItems: "center", gap: 14, flexWrap: "wrap" }}>
              <label style={{ display: "flex", alignItems: "center", gap: 7 }}>
                Base dispatch altitude (m above YP)
                <input
                  aria-label="Dispatch altitude offset in metres"
                  type="number"
                  min={0}
                  max={100}
                  step={1}
                  value={dispatchAltitudeOffset}
                  onChange={(event) => setDispatchAltitudeOffset(Math.max(0, Math.min(100, Math.floor(Number(event.target.value) || 0))))}
                  style={{ width: 72, background: "#1e293b", color: "white", border: "1px solid #475569", padding: 8, borderRadius: 4 }}
                />
              </label>
              <label style={{ display: "flex", alignItems: "center", gap: 7 }}>
                Circle radius (m)
                <input
                  aria-label="Circle radius in metres"
                  type="number"
                  min={1}
                  max={75}
                  step={1}
                  value={circleRadius}
                  onChange={(event) => setCircleRadius(Math.max(1, Math.min(75, Math.floor(Number(event.target.value) || 1))))}
                  style={{ width: 72, background: "#1e293b", color: "white", border: "1px solid #475569", padding: 8, borderRadius: 4 }}
                />
              </label>
              <label style={{ display: "flex", alignItems: "center", gap: 7 }}>
                Circle points
                <input
                  aria-label="Circle waypoint count"
                  type="number"
                  min={3}
                  max={100}
                  step={1}
                  value={circleWaypointCount}
                  onChange={(event) => setCircleWaypointCount(Math.max(3, Math.min(100, Math.floor(Number(event.target.value) || 3))))}
                  style={{ width: 72, background: "#1e293b", color: "white", border: "1px solid #475569", padding: 8, borderRadius: 4 }}
                />
              </label>
              <button
                type="button"
                onClick={generateCircle}
                title="Replace the current route with a circular track around the YP"
                style={{ display: "inline-flex", alignItems: "center", gap: 6, padding: 8, background: "#334155", color: "white", border: "1px solid #475569", borderRadius: 4 }}
              >
                <Circle size={15} /> Generate Circle
              </button>
              <label style={{ display: "flex", alignItems: "center", gap: 7 }}>
                <input
                  type="checkbox"
                  checked={faceInward}
                  onChange={(event) => setFaceInward(event.target.checked)}
                />
                Face inward toward YP
              </label>
              <label style={{ display: "flex", alignItems: "center", gap: 7 }}>
                <input
                  type="checkbox"
                  checked={holdLastWaypoint}
                  onChange={(event) => setHoldLastWaypoint(event.target.checked)}
                  title="Continue updating the final target relative to the moving YP until another command is sent"
                />
                Hold final relative waypoint
              </label>
              <label style={{ display: "flex", alignItems: "center", gap: 7 }}>
                Mission loops
                <input
                  aria-label="Mission loops"
                  type="number"
                  min={1}
                  max={100}
                  step={1}
                  value={missionLoops}
                  onChange={(event) => setMissionLoops(Math.max(1, Math.min(100, Math.floor(Number(event.target.value) || 1))))}
                  style={{ width: 72, background: "#1e293b", color: "white", border: "1px solid #475569", padding: 8, borderRadius: 4 }}
                />
              </label>
            </div>
            <div style={{ display: "flex", gap: 6 }}>
              <button type="button" title="Export local waypoint plan" aria-label="Export local waypoint plan" onClick={exportPlan} disabled={!waypoints.length} style={{ padding: 8, background: "#334155", color: "white", border: "1px solid #475569", borderRadius: 4, opacity: waypoints.length ? 1 : 0.5 }}>
                <Download size={16} />
              </button>
              <button type="button" title="Import local waypoint plan" aria-label="Import local waypoint plan" onClick={() => importFileRef.current?.click()} style={{ padding: 8, background: "#334155", color: "white", border: "1px solid #475569", borderRadius: 4 }}>
                <Upload size={16} />
              </button>
            </div>
          </div>
          <div style={{ display: "flex", gap: 10 }}>
            <select
              value={selectedVehicleId}
              onChange={(event) => setSelectedVehicleId(event.target.value)}
              style={{
                flex: 1,
                background: "#1e293b",
                color: "white",
                border: "1px solid #475569",
                padding: 10,
                borderRadius: 4,
              }}
            >
              <option value="">-- Select Vehicle --</option>
              {vehicles.map((vehicle) => (
                <option key={vehicle.vehicle_id} value={vehicle.vehicle_id}>
                  {vehicle.vehicle_id} ({vehicle.vehicle_type})
                </option>
              ))}
            </select>
            <button
              onClick={() => {
                setWaypoints([]);
                setSelectedId(null);
              }}
              style={{
                padding: 10,
                background: "#475569",
                color: "white",
                border: "none",
                borderRadius: 4,
              }}
            >
              Clear All
            </button>
            <button
              onClick={dispatch}
              style={{
                padding: 10,
                background: "#2563eb",
                color: "white",
                border: "none",
                borderRadius: 4,
                fontWeight: "bold",
              }}
            >
              Dispatch
            </button>
          </div>
          <input ref={importFileRef} type="file" accept="application/json,.json" onChange={importPlan} style={{ display: "none" }} />
        </div>
      </div>
    </div>
  );
}

function InteractiveWaypoint2D({
  waypoints,
  selectedId,
  orientationForced,
  onSelect,
  onAdd,
  onUpdate,
}: {
  waypoints: LocalWaypoint[];
  selectedId: string | null;
  orientationForced: boolean;
  onSelect: (id: string) => void;
  onAdd: (x: number, y: number) => void;
  onUpdate: (id: string, updates: Partial<LocalWaypoint>) => void;
}) {
  const ref = useRef<HTMLDivElement>(null);
  const width = 150;
  const height = 150;
  const add = (event: ReactPointerEvent<HTMLDivElement>) => {
    if (event.target !== ref.current || !ref.current) return;
    const rect = ref.current.getBoundingClientRect();
    onAdd(
      ((event.clientX - rect.left) / rect.width - 0.5) * width,
      -((event.clientY - rect.top) / rect.height - 0.5) * height,
    );
  };
  const drag = (id: string, event: ReactPointerEvent<HTMLDivElement>) => {
    event.stopPropagation();
    onSelect(id);
    const target = event.currentTarget;
    target.setPointerCapture(event.pointerId);
    const move = (item: PointerEvent) => {
      if (!ref.current) return;
      const rect = ref.current.getBoundingClientRect();
      const x = Math.max(0, Math.min(item.clientX - rect.left, rect.width));
      const y = Math.max(0, Math.min(item.clientY - rect.top, rect.height));
      onUpdate(id, {
        x: (x / rect.width - 0.5) * width,
        y: -(y / rect.height - 0.5) * height,
      });
    };
    const up = () => {
      target.removeEventListener("pointermove", move);
      target.removeEventListener("pointerup", up);
    };
    target.addEventListener("pointermove", move);
    target.addEventListener("pointerup", up);
  };
  const yawFromPointer = (waypoint: LocalWaypoint, event: ReactPointerEvent<SVGCircleElement>) => {
    if (!ref.current) return;
    const rect = ref.current.getBoundingClientRect();
    const centerX = rect.left + (waypoint.x / width + 0.5) * rect.width;
    const centerY = rect.top + (-waypoint.y / height + 0.5) * rect.height;
    const yaw = Math.atan2(event.clientX - centerX, centerY - event.clientY) * (180 / Math.PI);
    onUpdate(waypoint.id, { yaw: (yaw + 360) % 360 });
  };
  const rotate = (waypoint: LocalWaypoint, event: ReactPointerEvent<SVGCircleElement>) => {
    event.stopPropagation();
    onSelect(waypoint.id);
    event.currentTarget.setPointerCapture(event.pointerId);
    yawFromPointer(waypoint, event);
  };
  const handleRotate = (waypoint: LocalWaypoint, event: ReactPointerEvent<SVGCircleElement>) => {
    if (event.buttons === 1) yawFromPointer(waypoint, event);
  };
  return (
    <div
      ref={ref}
      onPointerDown={add}
      style={{
        width: "100%",
        maxWidth: 400,
        aspectRatio: "1/1",
        backgroundColor: "#0f172a",
        backgroundImage:
          "linear-gradient(#334155 1px, transparent 1px), linear-gradient(90deg, #334155 1px, transparent 1px)",
        backgroundSize: "20px 20px",
        position: "relative",
        cursor: "crosshair",
        border: "2px solid #475569",
        borderRadius: 4,
        overflow: "hidden",
      }}
    >
      {[25, 50, 75].map((range) => (
        <div
          key={range}
          style={{
            position: "absolute",
            left: "50%",
            top: "50%",
            width: `${(2 * range / width) * 100}%`,
            aspectRatio: "1 / 1",
            border: "1px solid rgba(148, 163, 184, 0.65)",
            borderRadius: "50%",
            transform: "translate(-50%, -50%)",
            pointerEvents: "none",
          }}
        >
          <span style={{ position: "absolute", right: 3, top: "50%", transform: "translateY(-50%)", color: "#cbd5e1", background: "rgba(15, 23, 42, 0.8)", fontSize: 10, whiteSpace: "nowrap" }}>{range} m</span>
        </div>
      ))}
      <div
        style={{
          position: "absolute",
          top: "50%",
          left: "50%",
          width: `${(8 / width) * 100}%`,
          height: `${(33 / height) * 100}%`,
          transform: "translate(-50%, -50%)",
          backgroundImage: `url('${import.meta.env.BASE_URL}logos/YP.png')`,
          backgroundSize: "contain",
          backgroundPosition: "center",
          backgroundRepeat: "no-repeat",
          pointerEvents: "none",
          opacity: 0.8,
        }}
      />
      {waypoints.map((waypoint, index) => (
        <div
          key={waypoint.id}
          style={{
            position: "absolute",
            left: `${(waypoint.x / width + 0.5) * 100}%`,
            top: `${(-waypoint.y / height + 0.5) * 100}%`,
            width: 0,
            height: 0,
          }}
        >
          {(() => {
            const yaw = ((waypoint.yaw % 360) + 360) % 360;
            const angle = (yaw * Math.PI) / 180;
            const tipX = 36 + Math.sin(angle) * 29;
            const tipY = 36 - Math.cos(angle) * 29;
            const baseX = tipX - Math.sin(angle) * 9;
            const baseY = tipY + Math.cos(angle) * 9;
            const perpX = Math.cos(angle) * 4;
            const perpY = Math.sin(angle) * 4;
            const color = waypoint.id === selectedId ? "#38bdf8" : "#f59e0b";
            return (
              <svg
                viewBox="0 0 72 72"
                style={{
                  position: "absolute",
                  left: -36,
                  top: -36,
                  width: 72,
                  height: 72,
                  overflow: "visible",
                  pointerEvents: "none",
                  zIndex: 5,
                }}
              >
                <line x1="36" y1="36" x2={tipX} y2={tipY} stroke={color} strokeWidth="2.5" />
                <polygon
                  points={`${tipX},${tipY} ${baseX + perpX},${baseY + perpY} ${baseX - perpX},${baseY - perpY}`}
                  fill={color}
                />
                <circle
                  role="slider"
                  aria-label={`Waypoint ${index + 1} yaw`}
                  aria-valuemin={0}
                  aria-valuemax={359}
                  aria-valuenow={Math.round(yaw)}
                  aria-valuetext={`${Math.round(yaw)} deg clockwise from ship bow`}
                  aria-disabled={orientationForced}
                  tabIndex={orientationForced ? -1 : 0}
                  cx={tipX}
                  cy={tipY}
                  r="5"
                  fill={color}
                  stroke="white"
                  strokeWidth="1.5"
                  style={{ pointerEvents: orientationForced ? "none" : "all", cursor: orientationForced ? "default" : "grab", touchAction: "none" }}
                  onPointerDown={orientationForced ? undefined : (event) => rotate(waypoint, event)}
                  onPointerMove={orientationForced ? undefined : (event) => handleRotate(waypoint, event)}
                  onKeyDown={(event) => {
                    if (orientationForced) return;
                    if (event.key !== "ArrowLeft" && event.key !== "ArrowRight") return;
                    event.preventDefault();
                    const step = event.shiftKey ? 15 : 5;
                    onSelect(waypoint.id);
                    onUpdate(waypoint.id, {
                      yaw: (yaw + (event.key === "ArrowRight" ? step : -step) + 360) % 360,
                    });
                  }}
                >
                  <title>{orientationForced ? `Waypoint ${index + 1} facing YP: ${Math.round(yaw)} deg` : `Waypoint ${index + 1} yaw: ${Math.round(yaw)} deg; drag or use arrow keys`}</title>
                </circle>
              </svg>
            );
          })()}
          <div
            onPointerDown={(event) => drag(waypoint.id, event)}
            style={{
              position: "absolute",
              left: 0,
              top: 0,
              width: 18,
              height: 18,
              backgroundColor: waypoint.id === selectedId ? "#38bdf8" : "#ef4444",
              border:
                waypoint.id === selectedId
                  ? "2px solid white"
                  : "1px solid #7f1d1d",
              borderRadius: "50%",
              transform: "translate(-50%, -50%)",
              cursor: "grab",
              zIndex: waypoint.id === selectedId ? 10 : 1,
              display: "flex",
              justifyContent: "center",
              alignItems: "center",
              fontSize: 10,
              color: "white",
              fontWeight: "bold",
              userSelect: "none",
            }}
          >
            {index + 1}
          </div>
        </div>
      ))}
    </div>
  );
}

function AltitudeProfile({
  waypoints,
  selectedId,
  dispatchAltitudeOffset,
  onSelect,
  onUpdateAltitude,
}: {
  waypoints: LocalWaypoint[];
  selectedId: string | null;
  dispatchAltitudeOffset: number;
  onSelect: (id: string) => void;
  onUpdateAltitude: (id: string, altitude: number) => void;
}) {
  const ref = useRef<HTMLDivElement>(null);
  const max = 150;
  const drag = (id: string, event: ReactPointerEvent<HTMLDivElement>) => {
    event.stopPropagation();
    onSelect(id);
    const target = event.currentTarget;
    target.setPointerCapture(event.pointerId);
    const move = (item: PointerEvent) => {
      if (!ref.current) return;
      const rect = ref.current.getBoundingClientRect();
      onUpdateAltitude(
        id,
        Math.max(
          0,
          (1 -
            Math.max(0, Math.min(item.clientY - rect.top, rect.height)) /
              rect.height) *
            max,
        ),
      );
    };
    const up = () => {
      target.removeEventListener("pointermove", move);
      target.removeEventListener("pointerup", up);
    };
    target.addEventListener("pointermove", move);
    target.addEventListener("pointerup", up);
  };
  if (!waypoints.length)
    return (
      <div
        style={{
          padding: 20,
          color: "#64748b",
          textAlign: "center",
          height: "100%",
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
        }}
      >
        Click the 2D map to add waypoints.
      </div>
    );
  const points = waypoints.map((waypoint, index) => ({
    ...waypoint,
    altitude: waypoint.z + dispatchAltitudeOffset,
    x: waypoints.length === 1 ? 50 : (index / (waypoints.length - 1)) * 90 + 5,
    y: (1 - (waypoint.z + dispatchAltitudeOffset) / max) * 100,
  }));
  return (
    <div
      ref={ref}
      style={{
        width: "100%",
        height: "100%",
        position: "relative",
        touchAction: "none",
      }}
    >
      <svg
        width="100%"
        height="100%"
        preserveAspectRatio="none"
        viewBox="0 0 100 100"
      >
        {[0, 50, 100, 150].map((altitude) => (
          <g key={altitude}>
            <line x1="0" x2="100" y1={100 - (altitude / max) * 100} y2={100 - (altitude / max) * 100} stroke="#475569" strokeDasharray="2 3" vectorEffect="non-scaling-stroke" />
            <text x="1" y={Math.max(5, 100 - (altitude / max) * 100 - 1)} fill="#cbd5e1" fontSize="4">{altitude} m</text>
          </g>
        ))}
        {points.length > 1 && (
          <polyline
            points={points.map((point) => `${point.x} ${point.y}`).join(", ")}
            fill="none"
            stroke="#f59e0b"
            strokeWidth="1"
            vectorEffect="non-scaling-stroke"
          />
        )}
      </svg>
      {points.map((point, index) => (
        <div key={point.id}>
          <div
            onPointerDown={(event) => drag(point.id, event)}
            title={`Waypoint ${index + 1}: ${point.altitude.toFixed(0)} m above YP`}
            style={{
              position: "absolute",
              left: `${point.x}%`,
              top: `${point.y}%`,
              width: 18,
              height: 18,
              backgroundColor: point.id === selectedId ? "#38bdf8" : "#ef4444",
              border: point.id === selectedId ? "2px solid white" : "1px solid #7f1d1d",
              borderRadius: "50%",
              transform: "translate(-50%, -50%)",
              cursor: "ns-resize",
              display: "flex",
              justifyContent: "center",
              alignItems: "center",
              fontSize: 10,
              color: "white",
              fontWeight: "bold",
              zIndex: 2,
            }}
          >
            {index + 1}
          </div>
          <span
            style={{
              position: "absolute",
              left: `${point.x}%`,
              top: `${point.y}%`,
              transform: `translate(${index === points.length - 1 ? "-100%" : "10px"}, -18px)`,
              padding: "1px 3px",
              borderRadius: 2,
              color: "#f8fafc",
              background: "rgba(15, 23, 42, 0.88)",
              fontSize: 11,
              whiteSpace: "nowrap",
              pointerEvents: "none",
              zIndex: 3,
            }}
          >
            {point.altitude.toFixed(0)} m
          </span>
        </div>
      ))}
    </div>
  );
}
