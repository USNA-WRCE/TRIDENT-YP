import { useCallback, useEffect, useRef, useState } from "react";
import type { Dispatch, SetStateAction } from "react";

const PREFIX = "trident-yp:";

/** useState that survives reloads via localStorage; `isValid` rejects stale or corrupt stored values. */
export function usePersistentState<T>(
  key: string,
  initial: T,
  isValid: (value: unknown) => value is T = (value): value is T => typeof value === typeof initial,
): [T, Dispatch<SetStateAction<T>>] {
  const storageKey = PREFIX + key;
  const isValidRef = useRef(isValid);
  const [value, setValue] = useState<T>(() => {
    try {
      const raw = window.localStorage.getItem(storageKey);
      if (raw !== null) {
        const parsed: unknown = JSON.parse(raw);
        if (isValidRef.current(parsed)) return parsed;
      }
    } catch {
      // Unavailable or corrupt storage falls back to the default.
    }
    return initial;
  });

  useEffect(() => {
    try {
      window.localStorage.setItem(storageKey, JSON.stringify(value));
    } catch {
      // Persistence is best-effort.
    }
  }, [storageKey, value]);

  return [value, useCallback<Dispatch<SetStateAction<T>>>((next) => setValue(next), [])];
}
