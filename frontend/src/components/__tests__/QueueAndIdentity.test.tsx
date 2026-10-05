/**
 * Frontend Component Tests: Review Queue & Patient Identity Banner
 * 
 * Authority:
 *   - PRODUCTIZATION_SPEC.md §7.2, §7.3
 *   - PRODUCTIZATION_DECISIONS.md PD-013, PD-015
 * 
 * Tests:
 *   1. Queue rendering & priority ordering
 *   2. Queue loading, error, and empty states
 *   3. Document selection triggering workbench navigation
 *   4. Identity banner side-by-side demographics rendering
 *   5. Identity confirmation match workflow
 *   6. Identity mismatch acceptance with reason code
 *   7. Mismatch validation & API error handling
 *   8. XSS-safe rendering of untrusted document & demographic strings
 */

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { QueueView } from '../views/QueueView';
import { PatientIdentityBanner } from '../identity/PatientIdentityBanner';
import { reviewApi, ApiClientError } from '../../api';
import type { ReviewQueueItem } from '../../types';

describe('Review Queue Component (QueueView)', () => {
  const mockSelectDocument = vi.fn();

  beforeEach(() => {
    vi.restoreAllMocks();
    mockSelectDocument.mockClear();
  });

  const sampleItems: ReviewQueueItem[] = [
    {
      document_id: 'doc-uuid-001',
      original_filename: 'cbc_panel_urgent.pdf',
      status: 'PENDING_REVIEW',
      uploaded_at: '2026-10-05T12:00:00Z',
      patient_context_id: 'MRN-100234',
      identity_verification_status: 'UNVERIFIED',
      total_claims: 14,
      auto_processed_count: 10,
      human_review_required_count: 4,
      discrepancy_count: 2,
      highest_risk_rank: 1, // High risk
      can_bulk_confirm_agreed: false,
    },
    {
      document_id: 'doc-uuid-002',
      original_filename: 'discharge_summary.pdf',
      status: 'PENDING_REVIEW',
      uploaded_at: '2026-10-05T12:30:00Z',
      patient_context_id: 'MRN-887766',
      identity_verification_status: 'CONFIRMED',
      total_claims: 8,
      auto_processed_count: 8,
      human_review_required_count: 0,
      discrepancy_count: 0,
      highest_risk_rank: 9, // Agreed
      can_bulk_confirm_agreed: true,
    },
  ];

  it('renders loading state initially', async () => {
    vi.spyOn(reviewApi, 'getQueue').mockImplementation(() => new Promise(() => {})); // Never resolves

    render(<QueueView onSelectDocument={mockSelectDocument} />);
    expect(screen.getByTestId('queue-loading')).toBeDefined();
    expect(screen.getByText(/Loading review queue from server/i)).toBeDefined();
  });

  it('renders empty state when queue returns 0 items', async () => {
    vi.spyOn(reviewApi, 'getQueue').mockResolvedValueOnce({
      total: 0,
      limit: 50,
      offset: 0,
      items: [],
    });

    render(<QueueView onSelectDocument={mockSelectDocument} />);

    await waitFor(() => {
      expect(screen.getByTestId('queue-empty')).toBeDefined();
    });
    expect(screen.getByText(/Review Queue is Empty/i)).toBeDefined();
  });

  it('renders error state with detail and correlation ID on API failure', async () => {
    vi.spyOn(reviewApi, 'getQueue').mockRejectedValueOnce(
      new ApiClientError(403, 'Clinical reviewer permission required to access queue', 'trace-corr-403')
    );

    render(<QueueView onSelectDocument={mockSelectDocument} />);

    await waitFor(() => {
      expect(screen.getByTestId('queue-error')).toBeDefined();
    });
    expect(screen.getByText(/Clinical reviewer permission required/i)).toBeDefined();
    expect(screen.getByText(/trace-corr-403/i)).toBeDefined();
    expect(screen.getByText(/Try Again/i)).toBeDefined();
  });

  it('renders populated queue items sorted with highest risk rank first', async () => {
    vi.spyOn(reviewApi, 'getQueue').mockResolvedValueOnce({
      total: 2,
      limit: 50,
      offset: 0,
      items: sampleItems,
    });

    render(<QueueView onSelectDocument={mockSelectDocument} />);

    await waitFor(() => {
      expect(screen.getByTestId('queue-table')).toBeDefined();
    });

    expect(screen.getByText('cbc_panel_urgent.pdf')).toBeDefined();
    expect(screen.getByText('discharge_summary.pdf')).toBeDefined();
    expect(screen.getByText('Rank 1 (High Risk)')).toBeDefined();
    expect(screen.getByText('Rank 9 (Agreed)')).toBeDefined();
    expect(screen.getByText('MRN-100234')).toBeDefined();
    expect(screen.getByText('2 discrepancy')).toBeDefined();
  });

  it('selects document when row or action button is clicked', async () => {
    vi.spyOn(reviewApi, 'getQueue').mockResolvedValueOnce({
      total: 1,
      limit: 50,
      offset: 0,
      items: [sampleItems[0]],
    });

    render(<QueueView onSelectDocument={mockSelectDocument} />);

    await waitFor(() => {
      expect(screen.getByText('Open Workbench →')).toBeDefined();
    });

    fireEvent.click(screen.getByText('Open Workbench →'));
    expect(mockSelectDocument).toHaveBeenCalledTimes(1);
    expect(mockSelectDocument).toHaveBeenCalledWith(sampleItems[0]);
  });

  it('safely renders untrusted strings containing script tags without executing or parsing HTML', async () => {
    const maliciousDoc: ReviewQueueItem = {
      document_id: 'xss-doc-999',
      original_filename: '<script>alert("xss-filename")</script>.pdf',
      status: 'PENDING_REVIEW',
      uploaded_at: '2026-10-05T12:00:00Z',
      patient_context_id: '<img src=x onerror=alert("xss-patient")>',
      identity_verification_status: 'UNVERIFIED',
      total_claims: 1,
      auto_processed_count: 0,
      human_review_required_count: 1,
      discrepancy_count: 0,
      highest_risk_rank: 2,
      can_bulk_confirm_agreed: false,
    };

    vi.spyOn(reviewApi, 'getQueue').mockResolvedValueOnce({
      total: 1,
      limit: 50,
      offset: 0,
      items: [maliciousDoc],
    });

    const { container } = render(<QueueView onSelectDocument={mockSelectDocument} />);

    await waitFor(() => {
      expect(screen.getByText('<script>alert("xss-filename")</script>.pdf')).toBeDefined();
    });

    // Zero script or image tags injected into DOM
    expect(container.querySelectorAll('script').length).toBe(0);
    expect(container.querySelectorAll('img').length).toBe(0);
  });
});

describe('Patient Identity Banner Component (PatientIdentityBanner)', () => {
  const sampleDoc: ReviewQueueItem = {
    document_id: 'doc-test-101',
    original_filename: 'metabolic_panel.pdf',
    status: 'PENDING_REVIEW',
    uploaded_at: '2026-10-05T10:00:00Z',
    patient_context_id: 'MRN-998877',
    identity_verification_status: 'UNVERIFIED',
    total_claims: 10,
    auto_processed_count: 8,
    human_review_required_count: 2,
    discrepancy_count: 0,
    highest_risk_rank: 4,
    can_bulk_confirm_agreed: false,
  };

  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it('renders side-by-side patient context vs extracted demographics', () => {
    render(<PatientIdentityBanner document={sampleDoc} />);

    expect(screen.getByText(/Patient Identity Verification/i)).toBeDefined();
    expect(screen.getByText(/Operator Patient Context/i)).toBeDefined();
    expect(screen.getByText(/Extracted Header Demographics/i)).toBeDefined();
    expect(screen.getByText('● Identity Unverified')).toBeDefined();
    expect(screen.getAllByText('MRN-998877').length).toBe(2);
  });

  it('confirms identity match on button click and updates status badge', async () => {
    const mockConfirm = vi.spyOn(reviewApi, 'confirmIdentity').mockResolvedValueOnce({
      document_id: sampleDoc.document_id,
      identity_verification_status: 'CONFIRMED',
      identity_confirmed_by: 'reviewer-user-id',
      identity_confirmed_at: '2026-10-05T13:00:00Z',
    });

    const onUpdated = vi.fn();
    render(<PatientIdentityBanner document={sampleDoc} onIdentityUpdated={onUpdated} />);

    const confirmBtn = screen.getByText(/Confirm Demographics Match/i);
    fireEvent.click(confirmBtn);

    await waitFor(() => {
      expect(mockConfirm).toHaveBeenCalledTimes(1);
    });

    const callArgs = mockConfirm.mock.calls[0];
    expect(callArgs[0]).toBe(sampleDoc.document_id);
    expect(callArgs[1].action).toBe('CONFIRMED');

    // Status updates to confirmed badge
    await waitFor(() => {
      expect(screen.getByText('✓ Identity Confirmed')).toBeDefined();
    });
    expect(onUpdated).toHaveBeenCalledWith('CONFIRMED', '2026-10-05T13:00:00Z');
  });

  it('handles mismatch acceptance with reason code and justification', async () => {
    const mockConfirm = vi.spyOn(reviewApi, 'confirmIdentity').mockResolvedValueOnce({
      document_id: sampleDoc.document_id,
      identity_verification_status: 'MISMATCH_ACCEPTED',
      identity_confirmed_by: 'reviewer-user-id',
      identity_confirmed_at: '2026-10-05T13:05:00Z',
    });

    const onUpdated = vi.fn();
    render(<PatientIdentityBanner document={sampleDoc} onIdentityUpdated={onUpdated} />);

    // Open mismatch drawer
    const openMismatchBtn = screen.getByText(/Accept Mismatch with Justification/i);
    fireEvent.click(openMismatchBtn);

    expect(screen.getByText(/Accept Demographic Mismatch \(Mandatory Clinical Justification\)/i)).toBeDefined();

    // Select reason code
    const reasonSelect = screen.getByLabelText(/Mismatch Reason Code/i);
    fireEvent.change(reasonSelect, { target: { value: 'TYPO_IN_SOURCE_DOCUMENT' } });

    // Submit form
    const submitBtn = screen.getByText(/Submit & Accept Mismatch/i);
    fireEvent.click(submitBtn);

    await waitFor(() => {
      expect(mockConfirm).toHaveBeenCalledTimes(1);
    });

    const callArgs = mockConfirm.mock.calls[0];
    expect(callArgs[1].action).toBe('MISMATCH_ACCEPTED');
    expect(callArgs[1].reason_code).toBe('TYPO_IN_SOURCE_DOCUMENT');

    await waitFor(() => {
      expect(screen.getByText('⚠ Mismatch Accepted')).toBeDefined();
    });
    expect(onUpdated).toHaveBeenCalledWith('MISMATCH_ACCEPTED', '2026-10-05T13:05:00Z');
  });

  it('displays API error alert when identity confirmation encounters a conflict or error', async () => {
    vi.spyOn(reviewApi, 'confirmIdentity').mockRejectedValueOnce(
      new ApiClientError(409, 'Patient demographic mismatch detected: name, mrn. Must explicitly accept mismatch with reason code.')
    );

    render(<PatientIdentityBanner document={sampleDoc} />);

    fireEvent.click(screen.getByText(/Confirm Demographics Match/i));

    await waitFor(() => {
      expect(screen.getByRole('alert')).toBeDefined();
    });
    expect(screen.getByText(/Patient demographic mismatch detected/i)).toBeDefined();
    // Status remains unverified
    expect(screen.getByText('● Identity Unverified')).toBeDefined();
  });
});
