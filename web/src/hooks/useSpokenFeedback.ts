import { useCallback, useEffect, useRef, useState } from "react";

import { synthesizeVoiceFeedback } from "../api";

const STORAGE_KEY = "trident-yp-spoken-feedback";

function readSavedPreference(): boolean {
  try {
    return window.localStorage.getItem(STORAGE_KEY) === "true";
  } catch {
    return false;
  }
}

type AudioContextWindow = Window & typeof globalThis & {
  webkitAudioContext?: typeof AudioContext;
};

function audioContextConstructor(): typeof AudioContext | undefined {
  const audioWindow = window as AudioContextWindow;
  return audioWindow.AudioContext ?? audioWindow.webkitAudioContext;
}

export function useSpokenFeedback() {
  const [enabled, setEnabled] = useState(readSavedPreference);
  const [error, setError] = useState("");
  const enabledRef = useRef(enabled);
  const contextRef = useRef<AudioContext | null>(null);
  const queueRef = useRef<Promise<void>>(Promise.resolve());
  const nextStartTimeRef = useRef(0);

  useEffect(() => {
    enabledRef.current = enabled;
    try {
      window.localStorage.setItem(STORAGE_KEY, String(enabled));
    } catch {
      return;
    }
  }, [enabled]);

  useEffect(() => () => {
    const context = contextRef.current;
    contextRef.current = null;
    if (context && context.state !== "closed") void context.close();
  }, []);

  const speak = useCallback((text: string) => {
    if (!enabledRef.current || !text.trim()) return;
    queueRef.current = queueRef.current
      .then(async () => {
        const context = contextRef.current;
        if (!enabledRef.current || !context || context.state !== "running") return;
        const audio = await synthesizeVoiceFeedback(text);
        if (!enabledRef.current || contextRef.current !== context || context.state !== "running") return;
        const decoded = await context.decodeAudioData(audio);
        const source = context.createBufferSource();
        source.buffer = decoded;
        source.connect(context.destination);
        const startAt = Math.max(context.currentTime, nextStartTimeRef.current);
        source.start(startAt);
        nextStartTimeRef.current = startAt + decoded.duration;
        setError("");
      })
      .catch((cause: unknown) => {
        setError(cause instanceof Error ? cause.message : "Spoken feedback is unavailable.");
      });
  }, []);

  const activateSavedAudio = useCallback(async () => {
    if (!enabledRef.current) return;
    const current = contextRef.current;
    if (current?.state === "running") return;

    const AudioContext = audioContextConstructor();
    if (!AudioContext) {
      setError("This browser does not support spoken feedback.");
      return;
    }
    try {
      const context = current && current.state !== "closed" ? current : new AudioContext();
      contextRef.current = context;
      await context.resume();
      if (contextRef.current === context && context.state === "running") setError("");
    } catch {
      setError("Could not enable audio output in this browser.");
    }
  }, []);

  useEffect(() => {
    if (!enabled) return;
    const activateOnGesture = (event: Event) => {
      if (event.target instanceof Element && event.target.closest("[data-spoken-feedback-toggle]")) return;
      void activateSavedAudio();
    };
    window.addEventListener("pointerdown", activateOnGesture, true);
    window.addEventListener("keydown", activateOnGesture, true);
    return () => {
      window.removeEventListener("pointerdown", activateOnGesture, true);
      window.removeEventListener("keydown", activateOnGesture, true);
    };
  }, [activateSavedAudio, enabled]);

  const toggleEnabled = useCallback(async () => {
    setError("");
    if (enabledRef.current) {
      enabledRef.current = false;
      setEnabled(false);
      nextStartTimeRef.current = 0;
      const current = contextRef.current;
      contextRef.current = null;
      if (current && current.state !== "closed") await current.close();
      return;
    }

    const AudioContext = audioContextConstructor();
    if (!AudioContext) {
      setError("This browser does not support spoken feedback.");
      return;
    }
    try {
      const context = new AudioContext();
      await context.resume();
      contextRef.current = context;
      enabledRef.current = true;
      setEnabled(true);
      speak("LIFEGUARD Voice activated");
    } catch {
      setError("Could not enable audio output in this browser.");
    }
  }, [speak]);

  return { enabled, error, speak, toggleEnabled };
}
