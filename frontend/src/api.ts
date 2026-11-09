const JSON_CONTENT_TYPE = 'application/json';

export class ApiError extends Error {
  readonly status: number;
  readonly data: unknown;

  constructor(message: string, status: number, data: unknown) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
    this.data = data;
  }
}

function getCookie(name: string): string | null {
  const prefix = `${name}=`;
  const match = document.cookie
    .split(';')
    .map((part) => part.trim())
    .find((part) => part.startsWith(prefix));

  return match ? decodeURIComponent(match.slice(prefix.length)) : null;
}

function errorMessage(data: unknown, status: number): string {
  if (typeof data === 'string' && data.trim()) {
    return data;
  }
  if (data && typeof data === 'object') {
    const record = data as Record<string, unknown>;
    if (typeof record.detail === 'string') {
      return record.detail;
    }
    if (typeof record.error === 'string') {
      return record.error;
    }
  }
  return `Request failed (${status})`;
}

/**
 * Fetch wrapper for the Django API.
 *
 * - Always sends session cookies.
 * - Adds the CSRF header to unsafe requests, which DRF's session auth requires.
 * - Only parses JSON when the server actually returned JSON, so a Vite/HTML
 *   fallback response produces a useful error instead of a parse exception.
 */
export async function apiFetch<T>(
  path: string,
  init: RequestInit = {},
): Promise<T> {
  const method = (init.method ?? 'GET').toUpperCase();
  const headers = new Headers(init.headers);
  headers.set('Accept', JSON_CONTENT_TYPE);

  if (init.body !== undefined && !headers.has('Content-Type')) {
    headers.set('Content-Type', JSON_CONTENT_TYPE);
  }

  if (method !== 'GET' && method !== 'HEAD') {
    const token = getCookie('csrftoken');
    if (token) {
      headers.set('X-CSRFToken', token);
    }
  }

  const response = await fetch(path, {
    ...init,
    method,
    headers,
    credentials: 'same-origin',
  });

  const contentType = response.headers.get('content-type') ?? '';
  const data = contentType.includes(JSON_CONTENT_TYPE)
    ? await response.json()
    : await response.text();

  if (!response.ok) {
    throw new ApiError(errorMessage(data, response.status), response.status, data);
  }

  return data as T;
}
