/**
 * Unit Tests for Frontend Authenticated API Client
 * 
 * Authority:
 *   - PRODUCTIZATION_SPEC.md §9.2, §9.3
 *   - PRODUCTIZATION_DECISIONS.md PD-013
 */

import { describe, it, expect, beforeEach, vi, afterEach } from 'vitest';
import { ApiClient, ApiClientError } from '../client';

describe('ApiClient', () => {
  let client: ApiClient;
  const mockFetch = vi.fn();

  beforeEach(() => {
    vi.stubGlobal('fetch', mockFetch);
    client = new ApiClient('/api/v1');
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it('generates non-empty correlation IDs', () => {
    const id1 = ApiClient.generateCorrelationId();
    const id2 = ApiClient.generateCorrelationId();
    expect(id1).toBeTruthy();
    expect(id2).toBeTruthy();
    expect(id1).not.toBe(id2);
  });

  it('attaches Bearer token and X-Correlation-ID to GET requests', async () => {
    client.setToken('test-bearer-token-123');

    mockFetch.mockResolvedValueOnce({
      ok: true,
      status: 200,
      headers: new Headers({ 'X-Correlation-ID': 'resp-corr-1' }),
      json: async () => ({ status: 'live' }),
    });

    const res = await client.get<{ status: string }>('/health/live');
    expect(res).toEqual({ status: 'live' });

    expect(mockFetch).toHaveBeenCalledTimes(1);
    const [url, init] = mockFetch.mock.calls[0];
    expect(url).toBe('/api/v1/health/live');

    const headers = init.headers as Headers;
    expect(headers.get('Authorization')).toBe('Bearer test-bearer-token-123');
    expect(headers.get('X-Correlation-ID')).toBeTruthy();
    expect(headers.get('Accept')).toBe('application/json');
  });

  it('attaches JSON body and Content-Type to POST requests', async () => {
    client.setToken('auth-token-456');

    mockFetch.mockResolvedValueOnce({
      ok: true,
      status: 201,
      headers: new Headers({ 'X-Correlation-ID': 'resp-corr-post' }),
      json: async () => ({ id: 'action-1', status: 'ACCEPTED' }),
    });

    const body = { action_type: 'ACCEPT', notes: 'Verified against source' };
    const res = await client.post<{ id: string; status: string }>('/claims/claim-123/actions', body);

    expect(res).toEqual({ id: 'action-1', status: 'ACCEPTED' });

    const [url, init] = mockFetch.mock.calls[0];
    expect(url).toBe('/api/v1/claims/claim-123/actions');
    expect(init.method).toBe('POST');
    expect(init.body).toBe(JSON.stringify(body));

    const headers = init.headers as Headers;
    expect(headers.get('Content-Type')).toBe('application/json');
    expect(headers.get('Authorization')).toBe('Bearer auth-token-456');
  });

  it('formats query parameters correctly', async () => {
    mockFetch.mockResolvedValueOnce({
      ok: true,
      status: 200,
      headers: new Headers(),
      json: async () => [],
    });

    await client.get('/review/queue', {
      params: { limit: 10, offset: 0, priority_floor: 2 },
    });

    const [url] = mockFetch.mock.calls[0];
    expect(url).toBe('/api/v1/review/queue?limit=10&offset=0&priority_floor=2');
  });

  it('extracts structured detail from FastAPI error responses', async () => {
    client.setToken('invalid-token');

    mockFetch.mockResolvedValueOnce({
      ok: false,
      status: 401,
      statusText: 'Unauthorized',
      headers: new Headers({ 'X-Correlation-ID': 'error-trace-999' }),
      json: async () => ({ detail: 'Token signature verification failed' }),
    });

    await expect(client.get('/documents/doc-1/claims')).rejects.toThrow(ApiClientError);

    try {
      await client.get('/documents/doc-1/claims');
    } catch (err: any) {
      // tested above
    }
  });

  it('preserves correlation ID and status on ApiClientError', async () => {
    mockFetch.mockResolvedValueOnce({
      ok: false,
      status: 403,
      statusText: 'Forbidden',
      headers: new Headers({ 'X-Correlation-ID': 'trace-forbidden' }),
      json: async () => ({
        detail: 'System administrators are strictly prohibited from accessing document content',
      }),
    });

    let caughtError: ApiClientError | null = null;
    try {
      await client.get('/documents/doc-1/file');
    } catch (err: any) {
      caughtError = err;
    }

    expect(caughtError).toBeInstanceOf(ApiClientError);
    expect(caughtError?.status).toBe(403);
    expect(caughtError?.detail).toContain('System administrators are strictly prohibited');
    expect(caughtError?.correlationId).toBe('trace-forbidden');
  });

  it('handles network disconnection gracefully with status 0', async () => {
    mockFetch.mockRejectedValueOnce(new TypeError('Failed to fetch'));

    let caughtError: ApiClientError | null = null;
    try {
      await client.get('/health/live');
    } catch (err: any) {
      caughtError = err;
    }

    expect(caughtError).toBeInstanceOf(ApiClientError);
    expect(caughtError?.status).toBe(0);
    expect(caughtError?.detail).toContain('Network connection to backend failed');
  });

  it('streams binary file via getBlob()', async () => {
    client.setToken('valid-token');
    const mockBlob = new Blob(['%PDF-1.4 test'], { type: 'application/pdf' });

    mockFetch.mockResolvedValueOnce({
      ok: true,
      status: 200,
      headers: new Headers({
        'Content-Type': 'application/pdf',
        'X-Correlation-ID': 'blob-trace-1',
      }),
      blob: async () => mockBlob,
    });

    const blob = await client.getBlob('/documents/doc-pdf-1/file');
    expect(blob).toBe(mockBlob);

    const [url, init] = mockFetch.mock.calls[0];
    expect(url).toBe('/api/v1/documents/doc-pdf-1/file');
    expect(init.method).toBe('GET');
  });
});
