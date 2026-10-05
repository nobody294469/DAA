/**
 * WorkbenchView Component — Review Workbench Shell integrating PatientIdentityBanner,
 * High-DPI PDF.js Viewer, Interactive SVG Evidence Overlays, and the Split-Screen Claim Panel.
 * 
 * Authority:
 *   - PRODUCTIZATION_SPEC.md §7.3, §13.1
 *   - PRODUCTIZATION_DECISIONS.md PD-009, PD-010, PD-013, PD-015
 */

import React, { useState, useEffect, useCallback } from 'react';
import type {
  ClaimActionPayload,
  DocumentClaimItem,
  DocumentReviewSummary,
  IdentityVerificationStatus,
  ReviewQueueItem,
} from '../../types';
import { reviewApi, ApiClientError } from '../../api';
import { PatientIdentityBanner } from '../identity/PatientIdentityBanner';
import { PdfViewer } from '../viewer/PdfViewer';
import { ClaimPanel } from '../claims/ClaimPanel';

interface WorkbenchViewProps {
  document: ReviewQueueItem | null;
  onBackToQueue: () => void;
}

export const WorkbenchView: React.FC<WorkbenchViewProps> = ({
  document,
  onBackToQueue,
}) => {
  const [currentDoc, setCurrentDoc] = useState<ReviewQueueItem | null>(document);
  const [claims, setClaims] = useState<DocumentClaimItem[]>([]);
  const [reviewSummary, setReviewSummary] = useState<DocumentReviewSummary | null>(null);
  const [loadingClaims, setLoadingClaims] = useState<boolean>(false);
  const [actionLoading, setActionLoading] = useState<boolean>(false);
  const [claimsError, setClaimsError] = useState<string | null>(null);
  const [selectedClaimId, setSelectedClaimId] = useState<string | null>(null);
  const [selectedEvidenceId, setSelectedEvidenceId] = useState<string | null>(null);

  // Sync state if prop changes
  useEffect(() => {
    setCurrentDoc(document);
    setSelectedClaimId(null);
    setSelectedEvidenceId(null);
  }, [document]);

  // Refresh document claims & review summary
  const refreshData = useCallback(async () => {
    if (!currentDoc?.document_id) {
      setClaims([]);
      setReviewSummary(null);
      return;
    }

    setLoadingClaims(true);
    setClaimsError(null);

    try {
      const [claimsResp, summaryResp] = await Promise.all([
        reviewApi.getDocumentClaims(currentDoc.document_id),
        reviewApi.getReviewSummary(currentDoc.document_id).catch(() => null),
      ]);

      setClaims(claimsResp.claims || []);
      if (summaryResp) {
        setReviewSummary(summaryResp);
        setCurrentDoc((prev) =>
          prev
            ? {
                ...prev,
                status: summaryResp.status as any,
                identity_verification_status: summaryResp.identity_verification_status as any,
              }
            : null
        );
      }
      setLoadingClaims(false);
    } catch (err: any) {
      setLoadingClaims(false);
      if (err instanceof ApiClientError) {
        setClaimsError(err.message);
      } else {
        setClaimsError(err.message || 'Failed to fetch document claims');
      }
    }
  }, [currentDoc?.document_id]);

  useEffect(() => {
    refreshData();
  }, [refreshData]);

  if (!currentDoc) {
    return (
      <div className="workbench-empty-container">
        <div className="card empty-workbench-card">
          <div className="empty-icon">📄</div>
          <h3>No Document Selected</h3>
          <p>
            Please select a clinical document from the Triage Review Queue to open its
            verification workbench, inspect evidence spans, and confirm patient identity.
          </p>
          <button className="btn btn-primary" onClick={onBackToQueue}>
            ← Go to Review Queue
          </button>
        </div>
        <style>{`
          .workbench-empty-container {
            max-width: 600px;
            margin: 60px auto;
          }
          .empty-workbench-card {
            text-align: center;
            padding: 50px 30px;
            display: flex;
            flex-direction: column;
            align-items: center;
            gap: 16px;
          }
          .empty-icon {
            font-size: 3rem;
          }
          .empty-workbench-card p {
            color: var(--text-secondary);
            font-size: 0.9rem;
            line-height: 1.5;
          }
        `}</style>
      </div>
    );
  }

  const handleIdentityUpdated = (newStatus: IdentityVerificationStatus) => {
    setCurrentDoc((prev) =>
      prev ? { ...prev, identity_verification_status: newStatus } : null
    );
  };

  const handleSelectClaim = (claimId: string, evidenceId?: string | null) => {
    setSelectedClaimId(claimId);
    setSelectedEvidenceId(evidenceId || null);
  };

  const handleClaimAction = async (claimId: string, payload: ClaimActionPayload) => {
    setActionLoading(true);
    try {
      await reviewApi.submitClaimAction(claimId, payload);
      await refreshData();
    } finally {
      setActionLoading(false);
    }
  };

  const handleBulkConfirmAgreed = async () => {
    setActionLoading(true);
    try {
      const agreedClaims = claims.filter(
        (c) =>
          c.review_state === 'PENDING' &&
          c.triage?.is_bulk_confirmable === true &&
          c.verification?.label === 'SUPPORTED' &&
          c.verification?.stage !== 'C' &&
          !c.verification?.is_fallback
      );

      for (const c of agreedClaims) {
        await reviewApi.submitClaimAction(c.claim_id, {
          action: 'ACCEPT',
          expected_version: c.version,
        });
      }
      await refreshData();
    } finally {
      setActionLoading(false);
    }
  };

  const handleApproveDocument = async () => {
    if (!currentDoc?.document_id) return;
    setActionLoading(true);
    try {
      const resp = await reviewApi.submitDocumentApproval(currentDoc.document_id);
      setCurrentDoc((prev) => (prev ? { ...prev, status: resp.status as any } : null));
      await refreshData();
    } finally {
      setActionLoading(false);
    }
  };

  return (
    <div className="workbench-container" data-testid="workbench-view">
      {/* Navigation Breadcrumb / Context Bar */}
      <div className="workbench-top-bar">
        <button className="btn-back" onClick={onBackToQueue}>
          ← Back to Review Queue
        </button>
        <div className="active-doc-info">
          <span className="doc-title">{currentDoc.original_filename}</span>
          <span className="doc-id font-mono">({currentDoc.document_id})</span>
          <span className="badge badge-supported">{currentDoc.status}</span>
        </div>
      </div>

      {/* Patient Identity Confirmation Banner (§7.3, PD-015) */}
      <PatientIdentityBanner
        document={currentDoc}
        onIdentityUpdated={handleIdentityUpdated}
      />

      {/* Split-screen Workspace */}
      <div className="workbench-shell">
        {/* Left Pane: High-DPI Document Viewer Frame with SVG Evidence Overlays (Step 4) */}
        <section className="workbench-pane viewer-pane" style={{ flex: 1.2 }}>
          <div className="pane-header">
            <span className="pane-title">📄 Source Document Page Viewer</span>
            <span className="pane-hint">
              {claims.length} Extracted Claims • High-DPI Canvas & Synchronized Overlays
            </span>
          </div>
          <div className="pane-content viewer-content" style={{ padding: 0, height: '100%', display: 'flex' }}>
            <PdfViewer
              documentId={currentDoc.document_id}
              claims={claims}
              selectedClaimId={selectedClaimId}
              selectedEvidenceId={selectedEvidenceId}
              onSelectClaim={handleSelectClaim}
              className="workbench-pdf-viewer"
            />
          </div>
        </section>

        {/* Right Pane: Split-Screen Claim Panel & Review Actions (Step 5) */}
        <section className="workbench-pane claims-pane" style={{ flex: 1.0 }}>
          <div className="pane-header">
            <span className="pane-title">✓ Clinical Claims & Verification Decisions</span>
            <span className="pane-hint">
              {loadingClaims
                ? 'Loading claims...'
                : `${claims.length} Claims • Mode 0 Review`}
            </span>
          </div>
          <div className="pane-content claims-content" style={{ padding: '16px', overflowY: 'auto' }}>
            {claimsError && (
              <div
                style={{
                  padding: '12px',
                  background: '#fee2e2',
                  border: '1px solid #fca5a5',
                  borderRadius: 'var(--radius-sm)',
                  color: '#991b1b',
                  fontSize: '13px',
                  marginBottom: '12px',
                }}
              >
                Error loading claims: {claimsError}
              </div>
            )}

            <ClaimPanel
              documentId={currentDoc.document_id}
              claims={claims}
              selectedClaimId={selectedClaimId}
              onSelectClaim={handleSelectClaim}
              reviewSummary={reviewSummary}
              identityStatus={currentDoc.identity_verification_status}
              onClaimAction={handleClaimAction}
              onBulkConfirmAgreed={handleBulkConfirmAgreed}
              onApproveDocument={handleApproveDocument}
              loading={actionLoading || loadingClaims}
            />
          </div>
        </section>
      </div>

      <style>{`
        .workbench-container {
          display: flex;
          flex-direction: column;
          height: 100%;
          gap: 12px;
        }
        .workbench-top-bar {
          display: flex;
          justify-content: space-between;
          align-items: center;
          padding: 4px 0;
        }
        .btn-back {
          font-size: 0.85rem;
          font-weight: 600;
          color: var(--color-primary);
          padding: 4px 8px;
          border-radius: var(--radius-sm);
        }
        .btn-back:hover {
          background-color: var(--color-primary-light);
        }
        .active-doc-info {
          display: flex;
          align-items: center;
          gap: 8px;
        }
        .doc-title {
          font-size: 0.95rem;
          font-weight: 700;
          color: var(--color-text);
        }
        .doc-id {
          font-size: 0.75rem;
          color: var(--color-text-muted);
        }
        .font-mono {
          font-family: var(--font-mono);
        }
        .workbench-shell {
          display: flex;
          gap: 16px;
          flex: 1;
          min-height: 600px;
        }
        .workbench-pane {
          display: flex;
          flex-direction: column;
          background-color: var(--color-surface);
          border: 1px solid var(--color-border);
          border-radius: var(--radius-lg);
          box-shadow: var(--shadow-sm);
          overflow: hidden;
        }
        .pane-header {
          display: flex;
          justify-content: space-between;
          align-items: center;
          padding: 10px 16px;
          background-color: var(--color-bg);
          border-bottom: 1px solid var(--color-border);
        }
        .pane-title {
          font-size: 0.875rem;
          font-weight: 600;
          color: var(--color-text);
        }
        .pane-hint {
          font-size: 0.75rem;
          color: var(--color-text-muted);
        }
        .workbench-pdf-viewer {
          flex: 1;
          border: none;
          border-radius: 0;
        }
      `}</style>
    </div>
  );
};
