import { useLive } from "@/stores/live";

const MAX_BACKOFF_MS = 8000;

/** One shared connection for the whole app, reconnecting with backoff. */
export function connectLive(): () => void {
  let socket: WebSocket | null = null;
  let attempts = 0;
  let timer: ReturnType<typeof setTimeout> | undefined;
  let closed = false;

  const open = () => {
    const scheme = location.protocol === "https:" ? "wss" : "ws";
    socket = new WebSocket(`${scheme}://${location.host}/ws/live`);
    useLive.getState().setConnection("connecting");
    socket.onopen = () => {
      attempts = 0;
      useLive.getState().setConnection("open");
    };
    socket.onmessage = (event) => {
      const message = JSON.parse(event.data as string) as { type: string; data: unknown };
      useLive.getState().receive(message.type, message.data);
    };
    socket.onclose = () => {
      if (closed) return;
      useLive.getState().setConnection("closed");
      attempts += 1;
      timer = setTimeout(open, Math.min(MAX_BACKOFF_MS, 500 * 2 ** attempts));
    };
    socket.onerror = () => socket?.close();
  };

  open();
  return () => {
    closed = true;
    clearTimeout(timer);
    socket?.close();
  };
}
