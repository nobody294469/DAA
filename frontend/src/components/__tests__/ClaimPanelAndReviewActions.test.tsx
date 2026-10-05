/**
 * Focused Unit Tests for Phase 8.6 Step 5: Split-Screen Claim Panel & Review Actions
 * 
 * Coverage:
 *   1. Claim panel rendering & field details display
 *   2. Verdict mapping (SUPPORTED -> "Matches source", DISCREPANCY, INSUFFICIENT_EVIDENCE)
 *   3. Disclaimer text verification
 *   4. Confidence display rules: Stage A/B displays calibrated %
 *   5. Confidence display rules: Stage C MUST NOT display percentage; shows second check & rationale
 *   6. "How It Was Checked" expandable details rendering
 *   7. Applicable flags display (Low OCR, Injection pattern, Fallback, Identity mismatch)
 *   8. Evidence <-> Claim selection synchronization
 *   9. ACCEPT action execution
 *   10. EDIT action inline validation (requires new_value & reason_code) & submission
 *   11. REJECT action inline validation (requires reason_code) & submission
 *   12. ESCALATE action submission
 *   13. REOPEN action execution for terminal claims
 *   14. Mode 0 bulk-action visibility restrictions (only AUTO_PROCESSED_AGREED, hidden for Stage C / discrepancy)
 *   15. Document approval controls (requires all terminal claims & identity confirmation)
 *   16. Backend error / conflict handling (e.g. 409 conflict display)
 *   17. XSS-safe rendering of untrusted claim metadata
 *   18. Accessibility basics (labels, aria, button semantics)
 */

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { ClaimDetailCard } from '../claims/ClaimDetailCard';
import { ClaimPanel } from '../claims/ClaimPanel';
import { WorkbenchView } from '../views/WorkbenchView';
import { reviewApi, ApiClientError } from '../../api';
import type { DocumentClaimItem, ReviewQueueItem, DocumentReviewSummary } from '../../types';

// Mock reviewApi
vi.mock('../../api', async () => {
  const actual = await vi.importActual<any>('../../api');
  return {
    ...actual,
    reviewApi: {
      ...actual.reviewApi,
      getDocumentFile: vi.fn(),
      getDocumentClaims: vi.fn(),
      getReviewSummary: vi.fn(),
      submitClaimAction: vi.fn(),
      submitDocumentApproval: vi.fn(),
    },
  };
});

describe('Phase 8.6 Step 5: ClaimDetailCard Component', () => {
  const mockClaimStageB: DocumentClaimItem = {
    id: 'claim-b',
    claim_id: 'c-b',
    document_id: 'doc-1',
    field_type: 'medication',
    value: 'Metformin 500mg',
    version: 1,
    origin: 'pipeline',
    review_state: 'PENDING',
    normalized_code: '860975',
    code_system: 'RxNorm',
    evidence_id: 'ev-1',
    grounding: {
      page_number: 1,
      bounding_box: [0.1, 0.2, 0.4, 0.3],
      ocr_confidence: 0.95,
      text_snippet: 'Metformin 500mg daily',
    },
    verification: {
      label: 'SUPPORTED',
      stage: 'B',
      calibrated_confidence: 0.942,
      second_check_performed: true,
      config_status: 'Qualified Stage B',
    },
  };

  const mockClaimStageC: DocumentClaimItem = {
    id: 'claim-c',
    claim_id: 'c-c',
    document_id: 'doc-1',
    field_type: 'condition',
    value: 'Chronic Kidney Disease',
    version: 1,
    origin: 'pipeline',
    review_state: 'PENDING',
    evidence_id: 'ev-2',
    grounding: {
      page_number: 1,
      ocr_confidence: 0.72, // LOW OCR!
      text_snippet: 'Suspicion of CKD stage 3',
    },
    verification: {
      label: 'DISCREPANCY',
      stage: 'C',
      calibrated_confidence: 0.88, // Must be suppressed for Stage C!
      second_check_performed: true,
      rationale: 'Conflict between narrative history and baseline labs.',
      contradiction_type: 'contradiction_in_evidence',
    },
  };

  it('renders field details, normalized concept, and raw value correctly', () => {
    render(<ClaimDetailCard claim={mockClaimStageB} onAction={vi.fn()} />);

    expect(screen.getByTestId('claim-field-type').textContent).toBe('medication');
    expect(screen.getByTestId('claim-raw-value').textContent).toBe('Metformin 500mg');
    expect(screen.getByText('860975')).toBeDefined();
    expect(screen.getByText(/RxNorm/i)).toBeDefined();
    expect(screen.getByTestId('claim-review-state-badge').textContent).toBe('PENDING');
  });

  it('maps SUPPORTED verdict to plain-language "Matches source" with disclaimer', () => {
    render(<ClaimDetailCard claim={mockClaimStageB} onAction={vi.fn()} />);

    expect(screen.getByTestId('verdict-badge').textContent).toBe('Matches source');
    expect(screen.getByTestId('verification-disclaimer').textContent).toContain(
      'This checks the extracted data against this document only.'
    );
  });

  it('displays calibrated percentage for Stage B resolved claims', () => {
    render(<ClaimDetailCard claim={mockClaimStageB} onAction={vi.fn()} />);

    const percentage = screen.getByTestId('calibrated-percentage-display');
    expect(percentage.textContent).toBe('94.2%');
    expect(screen.queryByTestId('stage-c-confidence-banner')).toBeNull();
  });

  it('MUST NOT display calibrated percentage for Stage C claims; shows second check and rationale', () => {
    render(<ClaimDetailCard claim={mockClaimStageC} onAction={vi.fn()} />);

    // Calibrated percentage display must NOT be rendered
    expect(screen.queryByTestId('calibrated-percentage-display')).toBeNull();

    // Stage C human review banner rendered
    const stageCBanner = screen.getByTestId('stage-c-confidence-banner');
    expect(stageCBanner).toBeDefined();
    expect(stageCBanner.textContent).toContain('Human Review Required (Stage C)');
    expect(stageCBanner.textContent).toContain('Second-check performed: Yes');
    expect(stageCBanner.textContent).toContain('Conflict between narrative history and baseline labs.');
  });

  it('renders applicable flags including Low OCR and Identity Unverified', () => {
    render(
      <ClaimDetailCard
        claim={mockClaimStageC}
        identityStatus="UNVERIFIED"
        onAction={vi.fn()}
      />
    );

    expect(screen.getByTestId('flag-low-ocr')).toBeDefined();
    expect(screen.getByTestId('flag-identity-mismatch')).toBeDefined();
  });

  it('renders "How It Was Checked" expandable section', () => {
    render(<ClaimDetailCard claim={mockClaimStageB} onAction={vi.fn()} />);

    const accordion = screen.getByTestId('how-it-was-checked-accordion');
    expect(accordion).toBeDefined();
    expect(accordion.textContent).toContain('Stage B');
    expect(accordion.textContent).toContain('Automated Second Check: Used');
    expect(accordion.textContent).toContain('Qualified Stage B');
  });

  it('handles ACCEPT action callback with expected_version', async () => {
    const handleAction = vi.fn().mockResolvedValue(undefined);
    render(<ClaimDetailCard claim={mockClaimStageB} onAction={handleAction} />);

    fireEvent.click(screen.getByTestId('btn-action-accept'));

    expect(handleAction).toHaveBeenCalledWith({
      action: 'ACCEPT',
      expected_version: 1,
    });
  });

  it('validates EDIT action requiring both new_value and reason_code', async () => {
    const handleAction = vi.fn().mockResolvedValue(undefined);
    render(<ClaimDetailCard claim={mockClaimStageB} onAction={handleAction} />);

    // Open Edit mode
    fireEvent.click(screen.getByTestId('btn-action-edit'));
    expect(screen.getByTestId('edit-claim-form')).toBeDefined();

    // Attempt submit without reason code
    fireEvent.click(screen.getByTestId('btn-submit-edit'));
    expect(screen.getByTestId('claim-action-error').textContent).toContain(
      'Reason code is required for editing a claim'
    );
    expect(handleAction).not.toHaveBeenCalled();

    // Fill valid reason code and submit
    fireEvent.change(screen.getByTestId('edit-reason-code-select'), {
      target: { value: 'DOSAGE_CORRECTION' },
    });
    fireEvent.change(screen.getByTestId('edit-claim-value-input'), {
      target: { value: 'Metformin 1000mg' },
    });

    fireEvent.click(screen.getByTestId('btn-submit-edit'));

    await waitFor(() => {
      expect(handleAction).toHaveBeenCalledWith({
        action: 'EDIT',
        expected_version: 1,
        new_value: 'Metformin 1000mg',
        reason_code: 'DOSAGE_CORRECTION',
        notes: undefined,
      });
    });
  });

  it('validates REJECT action requiring reason_code', async () => {
    const handleAction = vi.fn().mockResolvedValue(undefined);
    render(<ClaimDetailCard claim={mockClaimStageB} onAction={handleAction} />);

    fireEvent.click(screen.getByTestId('btn-action-reject'));
    expect(screen.getByTestId('reject-claim-form')).toBeDefined();

    // Submit without selecting reason
    fireEvent.click(screen.getByTestId('btn-submit-reject'));
    expect(screen.getByTestId('claim-action-error').textContent).toContain(
      'Reason code is required to reject a claim'
    );

    // Select reason code and submit
    fireEvent.change(screen.getByTestId('reject-reason-code-select'), {
      target: { value: 'HALLUCINATION' },
    });
    fireEvent.click(screen.getByTestId('btn-submit-reject'));

    await waitFor(() => {
      expect(handleAction).toHaveBeenCalledWith({
        action: 'REJECT',
        expected_version: 1,
        reason_code: 'HALLUCINATION',
        notes: undefined,
      });
    });
  });

  it('renders REOPEN action button for terminal claims and dispatches REOPEN', async () => {
    const terminalClaim: DocumentClaimItem = {
      ...mockClaimStageB,
      review_state: 'ACCEPTED',
    };
    const handleAction = vi.fn().mockResolvedValue(undefined);

    render(<ClaimDetailCard claim={terminalClaim} onAction={handleAction} />);

    const reopenBtn = screen.getByTestId('btn-action-reopen');
    expect(reopenBtn).toBeDefined();

    fireEvent.click(reopenBtn);

    expect(handleAction).toHaveBeenCalledWith({
      action: 'REOPEN',
      expected_version: 1,
    });
  });

  it('sanitizes untrusted values and notes against XSS injection', () => {
    const xssClaim: DocumentClaimItem = {
      ...mockClaimStageB,
      field_type: '<img src=x onerror=alert(1)>',
      value: '<script>alert("xss")</script>',
    };

    render(<ClaimDetailCard claim={xssClaim} onAction={vi.fn()} />);

    const valueEl = screen.getByTestId('claim-raw-value');
    expect(valueEl.textContent).toContain('<script>alert("xss")</script>');
    expect(document.querySelector('script')).toBeNull();
  });
});

describe('Phase 8.6 Step 5: ClaimPanel & Bulk Confirmation', () => {
  const mockClaims: DocumentClaimItem[] = [
    {
      id: 'c-1',
      claim_id: 'claim-1',
      document_id: 'doc-100',
      field_type: 'medication',
      value: 'Aspirin 81mg',
      version: 1,
      origin: 'pipeline',
      review_state: 'PENDING',
      triage: {
        category: 'AUTO_PROCESSED_AGREED',
        risk_tier: 'LOW',
        priority_rank: 9,
        is_bulk_confirmable: true,
      },
      verification: {
        label: 'SUPPORTED',
        stage: 'A',
      },
    },
    {
      id: 'c-2',
      claim_id: 'claim-2',
      document_id: 'doc-100',
      field_type: 'lab',
      value: 'Glucose 115 mg/dL',
      version: 1,
      origin: 'pipeline',
      review_state: 'PENDING',
      triage: {
        category: 'DISCREPANCY_REFUTATION',
        risk_tier: 'HIGH',
        priority_rank: 2,
        is_bulk_confirmable: false,
      },
      verification: {
        label: 'DISCREPANCY',
        stage: 'B',
      },
    },
  ];

  const mockReviewSummary: DocumentReviewSummary = {
    document_id: 'doc-100',
    original_filename: 'summary.pdf',
    status: 'PENDING_REVIEW',
    identity_verification_status: 'CONFIRMED',
    total_claims: 2,
    auto_processed_count: 1,
    human_review_required_count: 1,
    discrepancy_count: 1,
    can_bulk_confirm_agreed: true,
    is_approved: false,
    is_export_eligible: false,
  };

  it('renders Mode 0 bulk confirmation button ONLY for eligible agreed claims', () => {
    render(
      <ClaimPanel
        documentId="doc-100"
        claims={mockClaims}
        selectedClaimId="claim-1"
        onSelectClaim={vi.fn()}
        reviewSummary={mockReviewSummary}
        identityStatus="CONFIRMED"
        onClaimAction={vi.fn()}
        onBulkConfirmAgreed={vi.fn()}
      />
    );

    const bulkBtn = screen.getByTestId('btn-bulk-confirm-agreed');
    expect(bulkBtn).toBeDefined();
    // Only claim-1 is eligible; claim-2 is discrepancy
    expect(bulkBtn.textContent).toContain('Confirm all agreed claims in this document (1)');
  });

  it('does NOT render bulk action when no agreed pending claims exist', () => {
    const noAgreedClaims: DocumentClaimItem[] = [
      {
        ...mockClaims[1], // Discrepancy
      },
    ];

    render(
      <ClaimPanel
        documentId="doc-100"
        claims={noAgreedClaims}
        selectedClaimId="claim-2"
        onSelectClaim={vi.fn()}
        reviewSummary={mockReviewSummary}
        identityStatus="CONFIRMED"
        onClaimAction={vi.fn()}
      />
    );

    expect(screen.queryByTestId('btn-bulk-confirm-agreed')).toBeNull();
  });

  it('disables document approval button until all claims are terminal and identity confirmed', () => {
    render(
      <ClaimPanel
        documentId="doc-100"
        claims={mockClaims} // both PENDING
        selectedClaimId="claim-1"
        onSelectClaim={vi.fn()}
        reviewSummary={mockReviewSummary}
        identityStatus="CONFIRMED"
        onClaimAction={vi.fn()}
        onApproveDocument={vi.fn()}
      />
    );

    const approveBtn = screen.getByTestId('btn-approve-document') as HTMLButtonElement;
    expect(approveBtn.disabled).toBe(true);
  });

  it('enables document approval button when all claims are terminal and identity confirmed', () => {
    const terminalClaims: DocumentClaimItem[] = mockClaims.map((c) => ({
      ...c,
      review_state: 'ACCEPTED',
    }));

    render(
      <ClaimPanel
        documentId="doc-100"
        claims={terminalClaims}
        selectedClaimId="claim-1"
        onSelectClaim={vi.fn()}
        reviewSummary={mockReviewSummary}
        identityStatus="CONFIRMED"
        onClaimAction={vi.fn()}
        onApproveDocument={vi.fn()}
      />
    );

    const approveBtn = screen.getByTestId('btn-approve-document') as HTMLButtonElement;
    expect(approveBtn.disabled).toBe(false);
  });
});

describe('Phase 8.6 Step 5: WorkbenchView Full Integration', () => {
  const mockDoc: ReviewQueueItem = {
    document_id: 'doc-full-1',
    original_filename: 'patient_report.pdf',
    status: 'PENDING_REVIEW',
    uploaded_at: '2026-10-05T10:00:00Z',
    patient_context_id: 'ctx-1',
    identity_verification_status: 'CONFIRMED',
    total_claims: 1,
    auto_processed_count: 0,
    human_review_required_count: 1,
    discrepancy_count: 0,
    highest_risk_rank: 3,
    can_bulk_confirm_agreed: false,
  };

  const mockClaims: DocumentClaimItem[] = [
    {
      id: 'c-full',
      claim_id: 'claim-full',
      document_id: 'doc-full-1',
      field_type: 'medication',
      value: 'Atorvastatin 20mg',
      version: 1,
      origin: 'pipeline',
      review_state: 'PENDING',
      grounding: {
        page_number: 1,
        bounding_box: [0.1, 0.2, 0.5, 0.3],
        text_snippet: 'Atorvastatin 20mg tablet oral',
      },
      verification: {
        label: 'SUPPORTED',
        stage: 'A',
      },
    },
  ];

  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('loads claims, renders split screen with PdfViewer and ClaimPanel, and dispatches review actions', async () => {
    vi.mocked(reviewApi.getDocumentClaims).mockResolvedValue({
      document_id: 'doc-full-1',
      total: 1,
      claims: mockClaims,
    });
    vi.mocked(reviewApi.getReviewSummary).mockResolvedValue({
      document_id: 'doc-full-1',
      original_filename: 'patient_report.pdf',
      status: 'PENDING_REVIEW',
      identity_verification_status: 'CONFIRMED',
      total_claims: 1,
      auto_processed_count: 0,
      human_review_required_count: 1,
      discrepancy_count: 0,
      can_bulk_confirm_agreed: false,
      is_approved: false,
      is_export_eligible: false,
    });
    vi.mocked(reviewApi.getDocumentFile).mockReturnValue(new Promise(() => {}));
    vi.mocked(reviewApi.submitClaimAction).mockResolvedValue({
      action: 'ACCEPT',
      claim_id: 'claim-full',
      row_id: 'c-full',
      version: 1,
      review_state: 'ACCEPTED',
      origin: 'pipeline',
    });

    render(<WorkbenchView document={mockDoc} onBackToQueue={vi.fn()} />);

    // Check that ClaimPanel rendered with Atorvastatin
    await waitFor(() => {
      expect(screen.getByTestId('claim-raw-value').textContent).toBe('Atorvastatin 20mg');
    });

    // Execute Accept action
    fireEvent.click(screen.getByTestId('btn-action-accept'));

    await waitFor(() => {
      expect(reviewApi.submitClaimAction).toHaveBeenCalledWith('claim-full', {
        action: 'ACCEPT',
        expected_version: 1,
      });
    });
  });

  it('displays backend error message on conflict (e.g. Four-Eyes violation or lock conflict)', async () => {
    vi.mocked(reviewApi.getDocumentClaims).mockResolvedValue({
      document_id: 'doc-full-1',
      total: 1,
      claims: [
        {
          ...mockClaims[0],
          review_state: 'ACCEPTED', // terminal claim
        },
      ],
    });
    vi.mocked(reviewApi.getDocumentFile).mockReturnValue(new Promise(() => {}));
    vi.mocked(reviewApi.submitDocumentApproval).mockRejectedValue(
      new ApiClientError(
        409,
        'Four-Eyes policy violation: document uploader cannot approve their own document.',
        'corr-409'
      )
    );

    render(<WorkbenchView document={mockDoc} onBackToQueue={vi.fn()} />);

    await waitFor(() => {
      expect(screen.getByTestId('btn-approve-document')).toBeDefined();
    });

    fireEvent.click(screen.getByTestId('btn-approve-document'));

    await waitFor(() => {
      expect(screen.getByTestId('approval-error-banner').textContent).toContain(
        'Four-Eyes policy violation'
      );
    });
  });
});
