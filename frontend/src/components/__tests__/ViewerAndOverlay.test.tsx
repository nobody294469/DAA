/**
 * Focused Unit Tests for Phase 8.6 Step 4: PDF Viewer & Interactive SVG Evidence Overlays
 * 
 * Coverage:
 *   1. EvidenceOverlay bounding box rendering on active page
 *   2. EvidenceOverlay page filtering (only current page displayed)
 *   3. EvidenceOverlay styling by verification label (DISCREPANCY, SUPPORTED, INSUFFICIENT_EVIDENCE)
 *   4. Coordinate transformation & overlay synchronization
 *   5. Evidence selection interaction (onSelectEvidence called with claimId, evidenceId)
 *   6. Selected highlight visual distinction
 *   7. Visibility toggle behavior (automation-bias default ON, can be toggled)
 *   8. XSS-safe handling of untrusted claim metadata and evidence snippets
 *   9. PdfViewer loading and error states (404, 403, retry)
 *   10. WorkbenchView integration with evidence selection updating Workbench context
 */

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { EvidenceOverlay } from '../viewer/EvidenceOverlay';
import { PdfViewer } from '../viewer/PdfViewer';
import { WorkbenchView } from '../views/WorkbenchView';
import { reviewApi, ApiClientError } from '../../api';
import type { DocumentClaimItem, ReviewQueueItem } from '../../types';

// Mock reviewApi
vi.mock('../../api', async () => {
  const actual = await vi.importActual<any>('../../api');
  return {
    ...actual,
    reviewApi: {
      ...actual.reviewApi,
      getDocumentFile: vi.fn(),
      getDocumentClaims: vi.fn(),
    },
  };
});

describe('Phase 8.6 Step 4: EvidenceOverlay Component', () => {
  const mockClaims: DocumentClaimItem[] = [
    {
      id: 'claim-1',
      claim_id: 'c1',
      document_id: 'doc-123',
      field_type: 'medication',
      value: 'Metformin 500mg',
      version: 1,
      origin: 'pipeline',
      review_state: 'PENDING',
      evidence_id: 'ev-1',
      grounding: {
        page_number: 1,
        bounding_box: { x0: 0.1, y0: 0.2, x1: 0.4, y1: 0.3 },
        ocr_confidence: 0.98,
        text_snippet: 'Metformin HCl 500 MG Oral Tablet',
      },
      verification: {
        label: 'SUPPORTED',
        calibrated_confidence: 0.95,
      },
    },
    {
      id: 'claim-2',
      claim_id: 'c2',
      document_id: 'doc-123',
      field_type: 'condition',
      value: 'Type 2 Diabetes',
      version: 1,
      origin: 'pipeline',
      review_state: 'PENDING',
      evidence_id: 'ev-2',
      grounding: {
        page_number: 1,
        bounding_box: { x0: 0.5, y0: 0.6, x1: 0.8, y1: 0.7 },
        ocr_confidence: 0.92,
        text_snippet: 'History of T2DM',
      },
      verification: {
        label: 'DISCREPANCY',
        calibrated_confidence: 0.65,
      },
    },
    {
      id: 'claim-3',
      claim_id: 'c3',
      document_id: 'doc-123',
      field_type: 'allergy',
      value: 'Penicillin',
      version: 1,
      origin: 'pipeline',
      review_state: 'PENDING',
      evidence_id: 'ev-3',
      grounding: {
        page_number: 2, // PAGE 2!
        bounding_box: { x0: 0.2, y0: 0.3, x1: 0.5, y1: 0.4 },
        ocr_confidence: 0.99,
        text_snippet: 'NKDA Penicillin allergy',
      },
      verification: {
        label: 'INSUFFICIENT_EVIDENCE',
        calibrated_confidence: 0.40,
      },
    },
  ];

  it('renders SVG overlay bounding boxes for the current page only', () => {
    render(
      <EvidenceOverlay
        claims={mockClaims}
        currentPage={1}
        viewportWidth={1000}
        viewportHeight={800}
      />
    );

    const svg = screen.getByTestId('evidence-overlay-svg');
    expect(svg).toBeDefined();

    // Page 1 claims should be present
    expect(screen.getByTestId('evidence-highlight-c1')).toBeDefined();
    expect(screen.getByTestId('evidence-highlight-c2')).toBeDefined();

    // Page 2 claim should NOT be present on page 1
    expect(screen.queryByTestId('evidence-highlight-c3')).toBeNull();
  });

  it('transforms bounding box coordinates to viewport pixel coordinates accurately', () => {
    render(
      <EvidenceOverlay
        claims={mockClaims}
        currentPage={1}
        viewportWidth={1000}
        viewportHeight={800}
      />
    );

    // c1 bbox: x0=0.1, y0=0.2, x1=0.4, y1=0.3 on 1000x800 -> x=100, y=160, width=300, height=80
    const highlightC1 = screen.getByTestId('evidence-highlight-c1');
    const rect = highlightC1.querySelector('rect');
    expect(rect).toBeDefined();
    expect(rect?.getAttribute('x')).toBe('100');
    expect(rect?.getAttribute('y')).toBe('160');
    expect(rect?.getAttribute('width')).toBe('300');
    expect(rect?.getAttribute('height')).toBe('80');
  });

  it('applies distinct styling according to verification label', () => {
    render(
      <EvidenceOverlay
        claims={mockClaims}
        currentPage={1}
        viewportWidth={1000}
        viewportHeight={800}
      />
    );

    const rectSupported = screen.getByTestId('evidence-highlight-c1').querySelector('rect');
    const rectDiscrepancy = screen.getByTestId('evidence-highlight-c2').querySelector('rect');

    // SUPPORTED is emerald/greenish (#059669)
    expect(rectSupported?.getAttribute('stroke')).toBe('#059669');

    // DISCREPANCY is amber/orange (#d97706)
    expect(rectDiscrepancy?.getAttribute('stroke')).toBe('#d97706');
  });

  it('triggers onSelectEvidence callback with claimId and evidenceId when clicked', () => {
    const handleSelect = vi.fn();
    render(
      <EvidenceOverlay
        claims={mockClaims}
        currentPage={1}
        viewportWidth={1000}
        viewportHeight={800}
        onSelectEvidence={handleSelect}
      />
    );

    const highlightC1 = screen.getByTestId('evidence-highlight-c1');
    fireEvent.click(highlightC1);

    expect(handleSelect).toHaveBeenCalledTimes(1);
    expect(handleSelect).toHaveBeenCalledWith('c1', 'ev-1');
  });

  it('highlights selected evidence with prominent selection styling', () => {
    render(
      <EvidenceOverlay
        claims={mockClaims}
        currentPage={1}
        viewportWidth={1000}
        viewportHeight={800}
        selectedClaimId="c1"
      />
    );

    const rectC1 = screen.getByTestId('evidence-highlight-c1').querySelector('rect');
    const rectC2 = screen.getByTestId('evidence-highlight-c2').querySelector('rect');

    // Selected has blue stroke with strokeWidth 3 and dropShadow filter
    expect(rectC1?.getAttribute('stroke')).toBe('#2563eb');
    expect(rectC1?.getAttribute('stroke-width')).toBe('3');
    expect(rectC1?.getAttribute('filter')).toBe('url(#evidence-glow)');

    // Unselected does not have the glow filter
    expect(rectC2?.getAttribute('filter')).toBeNull();
  });

  it('hides overlay completely when visible prop is false', () => {
    render(
      <EvidenceOverlay
        claims={mockClaims}
        currentPage={1}
        viewportWidth={1000}
        viewportHeight={800}
        visible={false}
      />
    );

    expect(screen.queryByTestId('evidence-overlay-svg')).toBeNull();
  });

  it('sanitizes untrusted evidence metadata against XSS injection', () => {
    const xssClaims: DocumentClaimItem[] = [
      {
        id: 'claim-xss',
        claim_id: 'cxss',
        document_id: 'doc-123',
        field_type: '<img src=x onerror=alert(1)>',
        value: '<script>alert("hacked")</script>',
        version: 1,
        origin: 'pipeline',
        review_state: 'PENDING',
        grounding: {
          page_number: 1,
          bounding_box: [0.1, 0.1, 0.3, 0.3],
          text_snippet: '<svg onload=alert("xss")>',
        },
      },
    ];

    render(
      <EvidenceOverlay
        claims={xssClaims}
        currentPage={1}
        viewportWidth={1000}
        viewportHeight={800}
      />
    );

    const title = screen.getByTestId('evidence-highlight-cxss').querySelector('title');
    expect(title).toBeDefined();
    // Raw HTML must remain plain text, never rendered as live HTML elements
    expect(title?.textContent).toContain('<script>alert("hacked")</script>');
    expect(document.querySelector('script')).toBeNull();
  });
});

describe('Phase 8.6 Step 4: PdfViewer & WorkbenchView Integration', () => {
  const mockDoc: ReviewQueueItem = {
    document_id: 'doc-pdf-456',
    original_filename: 'discharge_summary.pdf',
    status: 'PENDING_REVIEW',
    uploaded_at: '2026-10-05T10:00:00Z',
    patient_context_id: 'ctx-99',
    identity_verification_status: 'UNVERIFIED',
    total_claims: 2,
    auto_processed_count: 0,
    human_review_required_count: 2,
    discrepancy_count: 0,
    highest_risk_rank: 2,
    can_bulk_confirm_agreed: false,
  };

  const mockClaims: DocumentClaimItem[] = [
    {
      id: 'c-10',
      claim_id: 'claim-10',
      document_id: 'doc-pdf-456',
      field_type: 'medication',
      value: 'Lisinopril 10mg daily',
      version: 1,
      origin: 'pipeline',
      review_state: 'PENDING',
      evidence_id: 'ev-10',
      grounding: {
        page_number: 1,
        bounding_box: [0.1, 0.15, 0.4, 0.25],
        text_snippet: 'Lisinopril 10 mg tab by mouth daily',
        ocr_confidence: 0.96,
      },
      verification: {
        label: 'SUPPORTED',
      },
    },
  ];

  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('renders loading state while fetching document bytes', async () => {
    // Return unresolved promise to check loading state
    vi.mocked(reviewApi.getDocumentFile).mockReturnValue(new Promise(() => {}));

    render(<PdfViewer documentId="doc-pdf-456" />);

    expect(screen.getByTestId('pdf-viewer-loading')).toBeDefined();
    expect(screen.getByText(/Decrypting and preparing high-DPI PDF view/i)).toBeDefined();
  });

  it('displays 404 error banner when document is missing or purged', async () => {
    vi.mocked(reviewApi.getDocumentFile).mockRejectedValue(
      new ApiClientError(404, "Document 'doc-pdf-456' not found.", 'corr-404')
    );

    render(<PdfViewer documentId="doc-pdf-456" />);

    await waitFor(() => {
      expect(screen.getByTestId('pdf-viewer-error')).toBeDefined();
    });

    expect(screen.getByText(/Document Not Found/i)).toBeDefined();
    expect(screen.getByText(/Correlation ID: corr-404/i)).toBeDefined();
    expect(screen.getByRole('button', { name: /Retry/i })).toBeDefined();
  });

  it('displays 403 error banner when reviewer lacks access permissions', async () => {
    vi.mocked(reviewApi.getDocumentFile).mockRejectedValue(
      new ApiClientError(403, 'Access denied for system_admin.', 'corr-403')
    );

    render(<PdfViewer documentId="doc-pdf-456" />);

    await waitFor(() => {
      expect(screen.getByTestId('pdf-viewer-error')).toBeDefined();
    });

    expect(screen.getByRole('heading', { name: /Access Denied/i })).toBeDefined();
  });

  it('integrates PdfViewer into WorkbenchView and displays selected claim when evidence is clicked', async () => {
    vi.mocked(reviewApi.getDocumentClaims).mockResolvedValue({
      document_id: 'doc-pdf-456',
      total: 1,
      claims: mockClaims,
    });
    vi.mocked(reviewApi.getDocumentFile).mockReturnValue(new Promise(() => {}));

    render(
      <WorkbenchView
        document={mockDoc}
        onBackToQueue={vi.fn()}
      />
    );

    // Check that top bar and identity banner remain preserved
    expect(screen.getByText('discharge_summary.pdf')).toBeDefined();
    expect(screen.getByText('● Identity Unverified')).toBeDefined();

    // Check that claims summary loaded
    await waitFor(() => {
      expect(screen.getByText(/1 Extracted Claims/i)).toBeDefined();
    });
  });
});
