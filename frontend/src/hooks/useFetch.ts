import { useCallback, useEffect, useRef, useState } from "react";

import { useLive } from "@/stores/live";

export interface FetchState<T> {
  data: T | null;
  error: string | null;
  loading: boolean;
  reload: () => void;
}

/** Load once per key. `version` lets a WebSocket event trigger a refresh, so nothing polls. */
export function useFetch<T>(load: () => Promise<T>, key: string, version = 0): FetchState<T> {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const loader = useRef(load);
  loader.current = load;
  const serial = useRef(0);

  const run = useCallback(() => {
    const mine = ++serial.current;
    loader.current()
      .then((result) => {
        if (mine !== serial.current) return;
        setData(result);
        setError(null);
      })
      .catch((failure: Error) => {
        if (mine === serial.current) setError(failure.message);
      })
      .finally(() => {
        if (mine === serial.current) setLoading(false);
      });
  }, []);

  useEffect(() => {
    setLoading(true);
    run();
  }, [key, run]);

  useEffect(() => {
    if (version > 0) run();
  }, [version, run]);

  return { data, error, loading, reload: run };
}

/** Run an action and report failures as a toast. */
export function useAction() {
  const toast = useLive((s) => s.toast);
  const [busy, setBusy] = useState(false);
  const run = useCallback(
    async <T,>(action: () => Promise<T>, success?: string): Promise<T | undefined> => {
      setBusy(true);
      try {
        const result = await action();
        if (success) toast("success", success);
        return result;
      } catch (failure) {
        toast("error", (failure as Error).message);
        return undefined;
      } finally {
        setBusy(false);
      }
    },
    [toast],
  );
  return { run, busy };
}
