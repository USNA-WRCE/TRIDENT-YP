import { Check, Loader2, Mic, MicOff, X } from "lucide-react";
import { useEffect, useRef, useState } from "react";

import { interpretVoiceCommand } from "../api";
import type { VoiceCommandPreview } from "../api";
import type { Command } from "../types";

interface VoiceLocation {
  latitude: number;
  longitude: number;
}

interface VoiceControlProps {
  canDispatch: boolean;
  selectedLocation: VoiceLocation | null;
  onClearLocation: () => void;
  onDispatch: (vehicleId: string, command: Command) => boolean;
}

export function VoiceControl({ canDispatch, selectedLocation, onClearLocation, onDispatch }: VoiceControlProps) {
  const [open, setOpen] = useState(false);
  const [recording, setRecording] = useState(false);
  const [processing, setProcessing] = useState(false);
  const [preview, setPreview] = useState<VoiceCommandPreview | null>(null);
  const [error, setError] = useState("");
  const recorderRef = useRef<MediaRecorder | null>(null);
  const streamRef = useRef<MediaStream | null>(null);
  const chunksRef = useRef<Blob[]>([]);
  const holdingRef = useRef(false);
  const timeoutRef = useRef<number | null>(null);

  const secure = typeof window !== "undefined" && window.isSecureContext;
  const hasMediaCapture = typeof navigator !== "undefined" && Boolean(navigator.mediaDevices?.getUserMedia) && typeof MediaRecorder !== "undefined";
  const canRecord = secure && hasMediaCapture && canDispatch;
  const unavailableMessage = !secure
    ? "Open the ground station over trusted HTTPS to use the microphone."
    : !hasMediaCapture
      ? "This browser does not provide microphone recording."
      : "Connect to the vehicle server before recording a command.";

  useEffect(() => () => {
    holdingRef.current = false;
    if (timeoutRef.current != null) window.clearTimeout(timeoutRef.current);
    recorderRef.current?.stop();
    streamRef.current?.getTracks().forEach((track) => track.stop());
  }, []);

  const dispatch = (candidate: VoiceCommandPreview) => {
    if (!onDispatch(candidate.vehicle_id, candidate.command)) {
      setError("The command was not sent. Check the active vehicle connection and operator permissions.");
      return;
    }
    setPreview(null);
    setError("");
    setOpen(false);
  };

  const interpret = async (audio: Blob, location: VoiceLocation | null) => {
    setProcessing(true);
    try {
      const candidate = await interpretVoiceCommand(audio, location);
      if (candidate.requires_confirmation) setPreview(candidate);
      else dispatch(candidate);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Voice command could not be interpreted.");
    } finally {
      setProcessing(false);
    }
  };

  const stopRecording = () => {
    holdingRef.current = false;
    if (timeoutRef.current != null) window.clearTimeout(timeoutRef.current);
    timeoutRef.current = null;
    if (recorderRef.current?.state === "recording") recorderRef.current.stop();
  };

  const startRecording = async () => {
    if (recording || processing) return;
    holdingRef.current = true;
    setError("");
    setPreview(null);
    if (!canRecord) {
      setError(unavailableMessage);
      holdingRef.current = false;
      return;
    }
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      streamRef.current = stream;
      const preferredType = ["audio/webm;codecs=opus", "audio/mp4", "audio/ogg;codecs=opus", "audio/webm"]
        .find((type) => MediaRecorder.isTypeSupported(type));
      const recorder = preferredType
        ? new MediaRecorder(stream, { mimeType: preferredType })
        : new MediaRecorder(stream);
      recorderRef.current = recorder;
      chunksRef.current = [];
      const location = selectedLocation;
      recorder.ondataavailable = (event) => {
        if (event.data.size) chunksRef.current.push(event.data);
      };
      recorder.onstop = () => {
        stream.getTracks().forEach((track) => track.stop());
        streamRef.current = null;
        recorderRef.current = null;
        setRecording(false);
        const audio = new Blob(chunksRef.current, { type: recorder.mimeType });
        chunksRef.current = [];
        if (audio.size) void interpret(audio, location);
        else setError("No microphone audio was captured.");
      };
      recorder.start();
      setRecording(true);
      timeoutRef.current = window.setTimeout(stopRecording, 12000);
      if (!holdingRef.current) stopRecording();
    } catch (cause) {
      streamRef.current?.getTracks().forEach((track) => track.stop());
      streamRef.current = null;
      recorderRef.current = null;
      setRecording(false);
      holdingRef.current = false;
      setError(cause instanceof Error ? cause.message : "Microphone access was denied.");
    }
  };

  const onKeyDown = (event: React.KeyboardEvent<HTMLButtonElement>) => {
    if ((event.key === " " || event.key === "Enter") && !event.repeat) {
      event.preventDefault();
      void startRecording();
    }
  };

  return (
    <div className="voice-control">
      <button
        className={open ? "icon-button active" : "icon-button"}
        type="button"
        aria-label="Voice command"
        aria-expanded={open}
        title="Voice command"
        onClick={() => { setOpen((value) => !value); setError(""); }}
      >
        <Mic size={19} />
      </button>
      {open && (
        <section className="voice-panel" aria-label="Voice command panel">
          <header className="voice-panel-header">
            <strong>Voice command</strong>
            <button className="icon-button" type="button" aria-label="Close voice command" onClick={() => { stopRecording(); setOpen(false); }}>
              <X size={17} />
            </button>
          </header>
          <div className="voice-location-row">
            <span>{selectedLocation ? `Map point ${selectedLocation.latitude.toFixed(5)}, ${selectedLocation.longitude.toFixed(5)}` : "No map point selected"}</span>
            {selectedLocation && <button type="button" className="text-action" onClick={onClearLocation}>Clear</button>}
          </div>
          <button
            className={recording ? "voice-record-button recording" : "voice-record-button"}
            type="button"
            aria-label={recording ? "Release to interpret command" : "Hold to speak"}
            title={recording ? "Release to interpret command" : "Hold to speak"}
            disabled={!canRecord || processing}
            onPointerDown={(event) => {
              event.preventDefault();
              event.currentTarget.setPointerCapture(event.pointerId);
              void startRecording();
            }}
            onPointerUp={stopRecording}
            onPointerCancel={stopRecording}
            onKeyDown={onKeyDown}
            onKeyUp={(event) => {
              if (event.key === " " || event.key === "Enter") {
                event.preventDefault();
                stopRecording();
              }
            }}
          >
            {processing ? <Loader2 className="spin" size={22} /> : recording ? <MicOff size={22} /> : <Mic size={22} />}
          </button>
          <div className="voice-status" aria-live="polite">
            {recording ? "Listening" : processing ? "Interpreting" : preview ? "Review command" : "Ready"}
          </div>
          {!canRecord && !error && <div className="voice-error" role="status">{unavailableMessage}</div>}
          {preview && (
            <div className="voice-preview">
              <strong>{preview.vehicle_id}</strong>
              <p>{preview.summary}</p>
              <div className="voice-preview-detail">{preview.command.type.replace(/_/g, " ")}</div>
              <div className="voice-preview-actions">
                <button className="voice-cancel-button" type="button" onClick={() => setPreview(null)}>
                  <X size={16} /> Cancel
                </button>
                <button className="voice-dispatch-button" type="button" onClick={() => dispatch(preview)}>
                  <Check size={16} /> Dispatch
                </button>
              </div>
            </div>
          )}
          {error && <div className="voice-error" role="alert">{error}</div>}
        </section>
      )}
    </div>
  );
}