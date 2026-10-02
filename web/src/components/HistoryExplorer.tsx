import { Activity, AlertTriangle, Database, FileUp, Loader2, RefreshCw } from "lucide-react";
import { useEffect, useState, type FormEvent } from "react";

import { listHistoricalVehicles } from "../api";
import { summarizeFlightLog } from "../services/flightLog";
import styles from "./HistoryExplorer.module.css";

interface AppliedRange {
  vehicleId: string;
  start: number;
  end: number;
  source: "influx" | "backup";
}

function toLocalInputValue(timestamp: number): string {
  const localDate = new Date(timestamp - new Date(timestamp).getTimezoneOffset() * 60_000);
  return localDate.toISOString().slice(0, 23);
}

function formatRetention(seconds: number): string {
  if (seconds >= 86_400 && seconds % 86_400 === 0) return `${seconds / 86_400} days`;
  if (seconds >= 3_600 && seconds % 3_600 === 0) return `${seconds / 3_600} hours`;
  if (seconds >= 60 && seconds % 60 === 0) return `${seconds / 60} minutes`;
  return `${seconds} seconds`;
}

export function HistoryExplorer() {
  const [refreshKey, setRefreshKey] = useState(0);
  const [vehicles, setVehicles] = useState<string[]>([]);
  const [retentionSeconds, setRetentionSeconds] = useState(0);
  const [vehicleId, setVehicleId] = useState("");
  const [startValue, setStartValue] = useState("");
  const [endValue, setEndValue] = useState("");
  const [source, setSource] = useState<"influx" | "backup">("influx");
  const [backupUrl, setBackupUrl] = useState("");
  const [backupName, setBackupName] = useState("");
  const [backupRange, setBackupRange] = useState<{ start: number; end: number } | null>(null);
  const [appliedRange, setAppliedRange] = useState<AppliedRange | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [rangeError, setRangeError] = useState("");

  useEffect(() => () => {
    if (backupUrl) URL.revokeObjectURL(backupUrl);
  }, [backupUrl]);

  useEffect(() => {
    const handleDataDeleted = () => setRefreshKey((key) => key + 1);
    window.addEventListener("influxdb-data-deleted", handleDataDeleted);
    return () => window.removeEventListener("influxdb-data-deleted", handleDataDeleted);
  }, []);

  useEffect(() => {
    let active = true;
    setLoading(true);
    setError("");
    listHistoricalVehicles()
      .then((catalog) => {
        if (!active) return;
        const nextVehicles = catalog.vehicles ?? [];
        const nextRetention = Math.max(0, Number(catalog.retention_seconds) || 0);
        const end = Date.now();
        const start = Math.ceil((end - Math.min(15 * 60_000, nextRetention * 1000)) / 1000) * 1000;
        setVehicles(nextVehicles);
        setRetentionSeconds(nextRetention);
        setVehicleId(nextVehicles[0] ?? "");
        setStartValue(toLocalInputValue(start));
        setEndValue(toLocalInputValue(end));
        setSource("influx");
        setAppliedRange(nextVehicles.length > 0 ? { vehicleId: nextVehicles[0], start, end, source: "influx" } : null);
      })
      .catch((loadError: unknown) => {
        if (active) setError(loadError instanceof Error ? loadError.message : "Unable to load historical vehicles");
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => { active = false; };
  }, [refreshKey]);

  const retentionStart = Date.now() - retentionSeconds * 1000;
  const loadBackup = async (file: File) => {
    setLoading(true);
    setError("");
    setRangeError("");
    try {
      const isGzip = file.name.toLowerCase().endsWith(".gz") || file.type === "application/gzip";
      if (isGzip && typeof DecompressionStream === "undefined") {
        throw new Error("This browser cannot decompress gzip flight logs.");
      }
      const content = isGzip
        ? await new Response(file.stream().pipeThrough(new DecompressionStream("gzip"))).text()
        : await file.text();
      const summary = summarizeFlightLog(content);
      const end = summary.lastTimestamp + 1;
      const blobUrl = URL.createObjectURL(new Blob([content], { type: "application/x-ndjson" }));
      setBackupUrl(blobUrl);
      setBackupName(file.name);
      setBackupRange({ start: summary.firstTimestamp, end });
      setVehicles(summary.vehicles);
      setVehicleId(summary.vehicles[0]);
      setRetentionSeconds(0);
      setSource("backup");
      setStartValue(toLocalInputValue(summary.firstTimestamp));
      setEndValue(toLocalInputValue(end));
      setAppliedRange({ vehicleId: summary.vehicles[0], start: summary.firstTimestamp, end, source: "backup" });
    } catch (loadError) {
      setError(loadError instanceof Error ? loadError.message : "Unable to load flight log backup.");
    } finally {
      setLoading(false);
    }
  };

  const handleSubmit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const start = new Date(startValue).getTime();
    const end = new Date(endValue).getTime();
    if (!vehicleId) {
      setRangeError("Select a vehicle with retained telemetry.");
    } else if (!Number.isFinite(start) || !Number.isFinite(end) || start >= end) {
      setRangeError("Choose a valid start and end time.");
    } else if (source === "backup" && backupRange && (start < backupRange.start || end > backupRange.end)) {
      setRangeError("The selected range must be inside the loaded backup's time range.");
    } else if (source === "influx" && (start < retentionStart || end > Date.now())) {
      setRangeError("The selected range must be inside the current InfluxDB retention window.");
    } else {
      setRangeError("");
      setAppliedRange({ vehicleId, start, end, source });
    }
  };

  const openmctUrl = appliedRange
    ? `/openmct.html?vehicle=${encodeURIComponent(appliedRange.vehicleId)}&start=${appliedRange.start}&end=${appliedRange.end}${appliedRange.source === "backup" ? `&backup=${encodeURIComponent(backupUrl)}` : ""}`
    : "";

  return (
    <section className={styles.explorer} aria-label="Historical telemetry explorer">
      <form className={styles.toolbar} onSubmit={handleSubmit}>
        <label className={styles.control}>
          <span>Vehicle</span>
          <select value={vehicleId} onChange={(event) => setVehicleId(event.target.value)} disabled={loading || vehicles.length === 0}>
            {vehicles.length === 0 && <option value="">No retained vehicles</option>}
            {vehicles.map((id) => <option key={id} value={id}>{id}</option>)}
          </select>
        </label>
        <label className={styles.control}>
          <span>Start</span>
          <input
            type="datetime-local"
            step={0.001}
            value={startValue}
            min={source === "backup" && backupRange ? toLocalInputValue(backupRange.start) : retentionSeconds > 0 ? toLocalInputValue(retentionStart) : undefined}
            max={endValue || undefined}
            onChange={(event) => setStartValue(event.target.value)}
            disabled={loading || vehicles.length === 0}
          />
        </label>
        <label className={styles.control}>
          <span>End</span>
          <input
            type="datetime-local"
            step={0.001}
            value={endValue}
            max={source === "backup" && backupRange ? toLocalInputValue(backupRange.end) : toLocalInputValue(Date.now())}
            min={startValue || undefined}
            onChange={(event) => setEndValue(event.target.value)}
            disabled={loading || vehicles.length === 0}
          />
        </label>
        <div className={styles.actions}>
          <button className={styles.loadButton} type="submit" disabled={loading || vehicles.length === 0}>
            <Activity size={16} /> Load history
          </button>
          <label className={styles.fileButton} title="Load a saved flight log backup">
            <FileUp size={16} /> Backup
            <input
              type="file"
              accept=".gz,.jsonl,.jsonl.gz,application/gzip,application/x-ndjson"
              onChange={(event) => {
                const file = event.currentTarget.files?.[0];
                event.currentTarget.value = "";
                if (file) void loadBackup(file);
              }}
              disabled={loading}
            />
          </label>
          {source === "backup" && (
            <button
              className={styles.refreshButton}
              type="button"
              title="Return to InfluxDB history"
              aria-label="Return to InfluxDB history"
              onClick={() => { setBackupUrl(""); setBackupName(""); setBackupRange(null); setAppliedRange(null); setSource("influx"); setRefreshKey((key) => key + 1); }}
            >
              <Database size={16} />
            </button>
          )}
          {source === "influx" && (
            <button className={styles.refreshButton} type="button" title="Refresh vehicle list" aria-label="Refresh vehicle list" onClick={() => setRefreshKey((key) => key + 1)} disabled={loading}>
              {loading ? <Loader2 className={styles.spin} size={17} /> : <RefreshCw size={17} />}
            </button>
          )}
        </div>
        {source === "backup" ? <span className={styles.retention}>Loaded backup: {backupName}</span> : retentionSeconds > 0 && <span className={styles.retention}>Available window: {formatRetention(retentionSeconds)}</span>}
        {rangeError && <span className={styles.error} role="alert">{rangeError}</span>}
      </form>

      {error ? (
        <div className={styles.emptyState} role="alert"><AlertTriangle size={20} />{error}</div>
      ) : loading ? (
        <div className={styles.emptyState}><Loader2 className={styles.spin} size={20} />Loading retained vehicles...</div>
      ) : vehicles.length === 0 && source === "influx" ? (
        <div className={styles.emptyState}>No vehicle telemetry is currently retained in InfluxDB.</div>
      ) : appliedRange ? (
        <iframe key={openmctUrl} className={styles.openmct} src={openmctUrl} title={`OpenMCT history for ${appliedRange.vehicleId}`} />
      ) : null}
    </section>
  );
}