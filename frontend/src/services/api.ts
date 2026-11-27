export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
    readonly code: string,
  ) {
    super(message);
  }
}

async function request<T>(method: string, path: string, body?: unknown): Promise<T> {
  let response: Response;
  try {
    response = await fetch(path, {
      method,
      headers: body === undefined ? undefined : { "Content-Type": "application/json" },
      body: body === undefined ? undefined : JSON.stringify(body),
    });
  } catch {
    throw new ApiError("The backend cannot be reached", 0, "network");
  }
  if (!response.ok) {
    const payload = await response.json().catch(() => null);
    const error = payload?.error;
    throw new ApiError(error?.message ?? `Request failed (${response.status})`, response.status, error?.code ?? "error");
  }
  return response.json() as Promise<T>;
}

export const api = {
  get: <T,>(path: string) => request<T>("GET", path),
  post: <T = unknown,>(path: string, body?: unknown) => request<T>("POST", path, body),
  put: <T = unknown,>(path: string, body?: unknown) => request<T>("PUT", path, body),
  patch: <T = unknown,>(path: string, body?: unknown) => request<T>("PATCH", path, body),
  delete: <T = unknown,>(path: string) => request<T>("DELETE", path),
};

export function query(params: Record<string, string | number | boolean | null | undefined>): string {
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value !== null && value !== undefined && value !== "") search.set(key, String(value));
  }
  const text = search.toString();
  return text ? `?${text}` : "";
}
