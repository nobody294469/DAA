/**
 * Authenticated REST API Client for Clindoc2FHIR Backend (/api/v1)
 * 
 * Authority:
 *   - PRODUCTIZATION_SPEC.md §9.2, §9.3
 *   - PRODUCTIZATION_DECISIONS.md PD-006, PD-009, PD-013
 * 
 * Guarantees:
 *   - Attaches Authorization Bearer token when present.
 *   - Attaches X-Correlation-ID for end-to-end request tracing.
 *   - Consistent error extraction from FastAPI responses.
 *   - Binary streaming support for document file viewing.
 */

export class ApiClientError extends Error {
  public readonly status: number;
  public readonly detail: string;
  public readonly correlationId?: string;

  constructor(status: number, detail: string, correlationId?: string) {
    super(`API Error ${status}: ${detail}`);
    this.name = 'ApiClientError';
    this.status = status;
    this.detail = detail;
    this.correlationId = correlationId;
  }
}

export interface RequestOptions extends RequestInit {
  token?: string | null;
  correlationId?: string;
  params?: Record<string, string | number | boolean | undefined>;
}

export class ApiClient {
  private baseUrl: string;
  private token: string | null = null;

  constructor(baseUrl: string = '/api/v1') {
    this.baseUrl = baseUrl.replace(/\/+$/, '');
  }

  /** Set active Bearer token for subsequent requests */
  public setToken(token: string | null): void {
    this.token = token;
  }

  /** Get currently configured token */
  public getToken(): string | null {
    return this.token;
  }

  /** Generate standard UUIDv4 correlation ID */
  public static generateCorrelationId(): string {
    if (typeof crypto !== 'undefined' && crypto.randomUUID) {
      return crypto.randomUUID();
    }
    return 'corr-' + Math.random().toString(36).substring(2, 15);
  }

  private buildUrl(endpoint: string, params?: Record<string, string | number | boolean | undefined>): string {
    const cleanEndpoint = endpoint.startsWith('/') ? endpoint : `/${endpoint}`;
    let fullUrl = `${this.baseUrl}${cleanEndpoint}`;

    if (params) {
      const searchParams = new URLSearchParams();
      for (const [key, value] of Object.entries(params)) {
        if (value !== undefined && value !== null) {
          searchParams.append(key, String(value));
        }
      }
      const qs = searchParams.toString();
      if (qs) {
        fullUrl += `?${qs}`;
      }
    }
    return fullUrl;
  }

  /** Execute authenticated HTTP request with JSON parsing */
  public async request<T>(endpoint: string, options: RequestOptions = {}): Promise<T> {
    const { token = this.token, correlationId = ApiClient.generateCorrelationId(), params, ...fetchInit } = options;

    const url = this.buildUrl(endpoint, params);
    const headers = new Headers(fetchInit.headers || {});

    // 1. Traceability: Attach X-Correlation-ID
    headers.set('X-Correlation-ID', correlationId);

    // 2. Authentication: Attach Bearer token
    if (token) {
      headers.set('Authorization', `Bearer ${token}`);
    }

    // 3. Content-Type default for JSON if body is string
    if (fetchInit.body && typeof fetchInit.body === 'string' && !headers.has('Content-Type')) {
      headers.set('Content-Type', 'application/json');
    }
    if (!headers.has('Accept')) {
      headers.set('Accept', 'application/json');
    }

    let response: Response;
    try {
      response = await fetch(url, {
        ...fetchInit,
        headers,
      });
    } catch (err: any) {
      throw new ApiClientError(
        0,
        `Network connection to backend failed: ${err.message || 'Server unreachable'}`,
        correlationId
      );
    }

    const respCorrelationId = response.headers.get('X-Correlation-ID') || correlationId;

    if (!response.ok) {
      let errorDetail = `HTTP ${response.status} ${response.statusText}`;
      try {
        const errorJson = await response.json();
        if (errorJson && typeof errorJson.detail === 'string') {
          errorDetail = errorJson.detail;
        } else if (errorJson && typeof errorJson.detail === 'object') {
          errorDetail = JSON.stringify(errorJson.detail);
        }
      } catch {
        // Response was not JSON
      }
      throw new ApiClientError(response.status, errorDetail, respCorrelationId);
    }

    if (response.status === 204) {
      return {} as T;
    }

    return response.json() as Promise<T>;
  }

  /** GET request shortcut */
  public get<T>(endpoint: string, options: RequestOptions = {}): Promise<T> {
    return this.request<T>(endpoint, { ...options, method: 'GET' });
  }

  /** POST request shortcut */
  public post<T>(endpoint: string, body?: any, options: RequestOptions = {}): Promise<T> {
    return this.request<T>(endpoint, {
      ...options,
      method: 'POST',
      body: body ? JSON.stringify(body) : undefined,
    });
  }

  /** Retrieve raw binary blob (e.g. document PDF or page image) */
  public async getBlob(endpoint: string, options: RequestOptions = {}): Promise<Blob> {
    const { token = this.token, correlationId = ApiClient.generateCorrelationId(), params, ...fetchInit } = options;

    const url = this.buildUrl(endpoint, params);
    const headers = new Headers(fetchInit.headers || {});
    headers.set('X-Correlation-ID', correlationId);

    if (token) {
      headers.set('Authorization', `Bearer ${token}`);
    }

    const response = await fetch(url, {
      ...fetchInit,
      method: 'GET',
      headers,
    });

    if (!response.ok) {
      let errorDetail = `Failed to download file (HTTP ${response.status})`;
      try {
        const errJson = await response.json();
        if (errJson && errJson.detail) errorDetail = errJson.detail;
      } catch {
        // ignore
      }
      throw new ApiClientError(response.status, errorDetail, response.headers.get('X-Correlation-ID') || correlationId);
    }

    return response.blob();
  }
}

// Global API client instance
export const api = new ApiClient(import.meta.env.VITE_API_BASE_URL || '/api/v1');
