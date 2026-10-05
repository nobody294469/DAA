/**
 * Clindoc2FHIR Domain API Endpoints
 * 
 * Authority:
 *   - PRODUCTIZATION_SPEC.md §7.3, §9.3
 *   - PRODUCTIZATION_DECISIONS.md PD-013
 */

import { api } from './client';
import type {
  ClaimActionPayload,
  ClaimActionResult,
  DocumentApprovalPayload,
  DocumentApprovalResponse,
  DocumentClaimsResponse,
  DocumentReviewSummary,
  ReviewQueueItem,
} from '../types';

export * from './client';

export interface HealthCheckResponse {
  status: string;
  service?: string;
  version?: string;
}

export const healthApi = {
  getLive: () => api.get<HealthCheckResponse>('/health/live'),
  getReady: () => api.get<HealthCheckResponse>('/health/ready'),
};

export const reviewApi = {
  /** Fetch documents awaiting human review (Mode 0) */
  getQueue: (params?: { limit?: number; offset?: number; priority_floor?: number; status_filter?: string }) =>
    api.get<{ total: number; limit: number; offset: number; items: ReviewQueueItem[] }>('/review/queue', { params }),

  /** Fetch claims and verification results for a document */
  getDocumentClaims: (documentId: string) =>
    api.get<DocumentClaimsResponse>(`/documents/${documentId}/claims`),

  /** Fetch document review context, active leases, four-eyes gate */
  getReviewSummary: (documentId: string) =>
    api.get<DocumentReviewSummary>(`/documents/${documentId}/review-summary`),

  /** Stream decrypted document file (PDF/raster) from EncryptedBlobStorage */
  getDocumentFile: (documentId: string) =>
    api.getBlob(`/documents/${documentId}/file`),

  /** Submit claim action: ACCEPT, EDIT, REJECT, ESCALATE, REOPEN */
  submitClaimAction: (
    claimId: string,
    payload: ClaimActionPayload
  ) => api.post<ClaimActionResult>(`/claims/${claimId}/actions`, payload),

  /** Confirm patient identity (CONFIRMED or MISMATCH_ACCEPTED) */
  confirmIdentity: (
    documentId: string,
    payload: {
      action: 'CONFIRMED' | 'MISMATCH_ACCEPTED';
      reason_code?: string;
      notes?: string;
      context_demographics?: { name?: string | null; mrn?: string | null; dob?: string | null; gender?: string | null };
      extracted_demographics?: { name?: string | null; mrn?: string | null; dob?: string | null; gender?: string | null };
    }
  ) => api.post<{
    document_id: string;
    identity_verification_status: string;
    identity_confirmed_by?: string | null;
    identity_confirmed_at?: string | null;
  }>(`/documents/${documentId}/identity-confirmation`, payload),

  /** Final document approval enforcing Four-Eyes and terminal claims */
  submitDocumentApproval: (
    documentId: string,
    payload?: DocumentApprovalPayload
  ) => api.post<DocumentApprovalResponse>(`/documents/${documentId}/approval`, payload || {}),
};
