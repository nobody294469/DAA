/**
 * ClaimPanel Component — Full Clinical Claim Review & Document Approval Panel
 * 
 * Authority:
 *   - PRODUCTIZATION_SPEC.md §7.3, §13.1
 *   - PRODUCTIZATION_DECISIONS.md PD-009, PD-010, PD-013, PD-015
 */

import React, { useState } from 'react';
import type {
  ClaimActionPayload,
  DocumentClaimItem,
  DocumentReviewSummary,
  IdentityVerificationStatus,
} from '../../types';
import { ClaimDetailCard, VERDICT_MAP } from './ClaimDetailCard';

export interface ClaimPanelProps {
  documentId: string;
  claims: DocumentClaimItem[];
  selectedClaimId: string | null;
  onSelectClaim: (claimId: string, evidenceId?: string | null) => void;
  reviewSummary?: DocumentReviewSummary | null;
  identityStatus?: IdentityVerificationStatus;
  userRole?: string;
  onClaimAction: (claimId: string, payload: ClaimActionPayload) => Promise<void>;
  onBulkConfirmAgreed?: () => Promise<void>;
  onApproveDocument?: () => Promise<void>;
  loading?: boolean;
}

export const ClaimPanel: React.FC<ClaimPanelProps> = ({
  documentId: _documentId,
  claims,
  selectedClaimId,
  onSelectClaim,
  reviewSummary,
  identityStatus,
  userRole = 'clinical_reviewer',
  onClaimAction,
  onBulkConfirmAgreed,
  onApproveDocument,
  loading = false,
}) => {
  const [filter, setFilter] = useState<'ALL' | 'PENDING' | 'RESOLVED'>('ALL');
  const [approvalError, setApprovalError] = useState<string | null>(null);
  const [approvalSuccess, setApprovalSuccess] = useState<boolean>(false);
  const [bulkActionLoading, setBulkActionLoading] = useState<boolean>(false);
  const [bulkActionMessage, setBulkActionMessage] = useState<string | null>(null);

  const selectedClaim = claims.find((c) => c.claim_id === selectedClaimId) || (claims.length > 0 ? claims[0] : null);

  // Filtered claims
  const filteredClaims = claims.filter((c) => {
    if (filter === 'PENDING') return c.review_state === 'PENDING';
    if (filter === 'RESOLVED') return c.review_state !== 'PENDING';
    return true;
  });

  // Calculate terminal claims progress
  const terminalCount = claims.filter(
    (c) => c.review_state === 'ACCEPTED' || c.review_state === 'REJECTED' || c.review_state === 'EDITED'
  ).length;
  const allClaimsTerminal = claims.length > 0 && terminalCount === claims.length;

  const isIdentityReady =
    identityStatus === 'CONFIRMED' || identityStatus === 'MISMATCH_ACCEPTED';

  // Mode 0 Bulk Action Qualification
  // Available ONLY when:
  // - claim is AUTO_PROCESSED_AGREED (is_bulk_confirmable === true)
  // - document allows bulk confirm (can_bulk_confirm_agreed === true)
  // - NOT offered for escalated, Stage C, discrepancy, or fallback!
  const agreedPendingClaims = claims.filter(
    (c) =>
      c.review_state === 'PENDING' &&
      c.triage?.is_bulk_confirmable === true &&
      c.verification?.label === 'SUPPORTED' &&
      c.verification?.stage !== 'C' &&
      !c.verification?.is_fallback
  );

  const canShowBulkAction =
    agreedPendingClaims.length > 0 &&
    (reviewSummary?.can_bulk_confirm_agreed ?? true);

  const handleBulkConfirm = async () => {
    if (!onBulkConfirmAgreed) return;
    setBulkActionLoading(true);
    setBulkActionMessage(null);
    try {
      await onBulkConfirmAgreed();
      setBulkActionMessage(`Confirmed ${agreedPendingClaims.length} agreed claims.`);
      setBulkActionLoading(false);
    } catch (err: any) {
      setBulkActionMessage(`Bulk action failed: ${err.message}`);
      setBulkActionLoading(false);
    }
  };

  const handleApproveDocument = async () => {
    setApprovalError(null);
    setApprovalSuccess(false);
    if (!onApproveDocument) return;

    try {
      await onApproveDocument();
      setApprovalSuccess(true);
    } catch (err: any) {
      setApprovalError(err.message || 'Failed to approve document');
    }
  };

  return (
    <div
      data-testid="claim-panel-container"
      style={{
        display: 'flex',
        flexDirection: 'column',
        height: '100%',
        gap: '12px',
        overflow: 'hidden',
      }}
    >
      {/* ─── CLAIMS LIST & FILTERS ────────────────────────────── */}
      <div
        style={{
          background: 'var(--color-surface)',
          border: '1px solid var(--color-border)',
          borderRadius: 'var(--radius-md)',
          padding: '12px',
          display: 'flex',
          flexDirection: 'column',
          gap: '8px',
          maxHeight: '260px',
        }}
      >
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
          <span style={{ fontWeight: 700, fontSize: '14px', color: 'var(--color-text)' }}>
            Document Claims ({claims.length})
          </span>
          <div style={{ display: 'flex', gap: '4px' }}>
            {(['ALL', 'PENDING', 'RESOLVED'] as const).map((tab) => (
              <button
                key={tab}
                data-testid={`filter-tab-${tab.toLowerCase()}`}
                onClick={() => setFilter(tab)}
                style={{
                  padding: '2px 8px',
                  fontSize: '11px',
                  fontWeight: 600,
                  background: filter === tab ? 'var(--color-primary)' : 'var(--color-bg)',
                  color: filter === tab ? '#fff' : 'var(--color-text-muted)',
                  border: '1px solid var(--color-border)',
                  borderRadius: 'var(--radius-sm)',
                  cursor: 'pointer',
                }}
              >
                {tab}
              </button>
            ))}
          </div>
        </div>

        {/* Scrollable list */}
        <div
          data-testid="claims-list"
          style={{
            overflowY: 'auto',
            display: 'flex',
            flexDirection: 'column',
            gap: '6px',
            paddingRight: '4px',
          }}
        >
          {filteredClaims.length === 0 ? (
            <div style={{ fontSize: '12px', color: 'var(--color-text-muted)', padding: '12px', textAlign: 'center' }}>
              No claims match the active filter.
            </div>
          ) : (
            filteredClaims.map((claim) => {
              const isSelected = selectedClaim?.claim_id === claim.claim_id;
              const verdict = (claim.verification?.label || '').toUpperCase();
              const vInfo = VERDICT_MAP[verdict] || { label: verdict, badgeClass: 'badge-unverified' };

              return (
                <div
                  key={claim.claim_id}
                  data-testid={`claim-list-item-${claim.claim_id}`}
                  onClick={() => onSelectClaim(claim.claim_id, claim.evidence_id)}
                  style={{
                    padding: '8px 10px',
                    borderRadius: 'var(--radius-sm)',
                    border: `1px solid ${isSelected ? 'var(--color-primary)' : 'var(--color-border)'}`,
                    background: isSelected ? 'rgba(37, 99, 235, 0.08)' : 'var(--color-bg)',
                    cursor: 'pointer',
                    display: 'flex',
                    justifyContent: 'space-between',
                    alignItems: 'center',
                    gap: '8px',
                  }}
                >
                  <div style={{ overflow: 'hidden' }}>
                    <div style={{ fontSize: '13px', fontWeight: 600, color: 'var(--color-text)' }}>
                      {claim.field_type}
                    </div>
                    <div
                      style={{
                        fontSize: '12px',
                        color: 'var(--color-text-muted)',
                        whiteSpace: 'nowrap',
                        overflow: 'hidden',
                        textOverflow: 'ellipsis',
                      }}
                    >
                      {claim.value}
                    </div>
                  </div>

                  <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'flex-end', gap: '2px' }}>
                    <span className={`badge ${vInfo.badgeClass}`} style={{ fontSize: '10px', padding: '1px 6px' }}>
                      {vInfo.label}
                    </span>
                    <span style={{ fontSize: '10px', color: 'var(--color-text-muted)', fontWeight: 600 }}>
                      {claim.review_state}
                    </span>
                  </div>
                </div>
              );
            })
          )}
        </div>

        {/* ─── MODE 0 BULK CONFIRMATION ACTION ───────────────── */}
        {canShowBulkAction && (
          <div
            data-testid="mode-0-bulk-container"
            style={{
              paddingTop: '8px',
              borderTop: '1px dashed var(--color-border)',
              display: 'flex',
              flexDirection: 'column',
              gap: '4px',
            }}
          >
            <button
              data-testid="btn-bulk-confirm-agreed"
              disabled={bulkActionLoading || loading}
              onClick={handleBulkConfirm}
              className="btn btn-secondary"
              style={{
                fontSize: '12px',
                padding: '6px 12px',
                background: 'rgba(5, 150, 105, 0.1)',
                border: '1px solid #10b981',
                color: '#059669',
                fontWeight: 600,
              }}
            >
              ✓ Confirm all agreed claims in this document ({agreedPendingClaims.length})
            </button>
            {bulkActionMessage && (
              <div style={{ fontSize: '11px', color: 'var(--color-text-muted)', textAlign: 'center' }}>
                {bulkActionMessage}
              </div>
            )}
          </div>
        )}
      </div>

      {/* ─── ACTIVE CLAIM DETAIL & REVIEW ACTIONS ────────────── */}
      <div style={{ flex: 1, overflowY: 'auto' }}>
        {selectedClaim ? (
          <ClaimDetailCard
            claim={selectedClaim}
            identityStatus={identityStatus}
            userRole={userRole}
            onAction={(payload) => onClaimAction(selectedClaim.claim_id, payload)}
            loading={loading}
          />
        ) : (
          <div
            style={{
              background: 'var(--color-surface)',
              border: '1px solid var(--color-border)',
              borderRadius: 'var(--radius-md)',
              padding: '24px',
              textAlign: 'center',
              color: 'var(--color-text-muted)',
              fontSize: '13px',
            }}
          >
            No claim selected. Select a claim from the list above or click an evidence highlight on the document page.
          </div>
        )}
      </div>

      {/* ─── DOCUMENT APPROVAL / REJECTION FOOTER ─────────────── */}
      <div
        data-testid="document-approval-footer"
        style={{
          background: 'var(--color-surface)',
          border: '1px solid var(--color-border)',
          borderRadius: 'var(--radius-md)',
          padding: '12px 16px',
          display: 'flex',
          flexDirection: 'column',
          gap: '8px',
        }}
      >
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
          <div style={{ fontSize: '12px', color: 'var(--color-text)' }}>
            <strong>Review Progress:</strong> {terminalCount} / {claims.length} claims terminal
          </div>
          <div style={{ fontSize: '12px', color: isIdentityReady ? '#059669' : '#d97706', fontWeight: 600 }}>
            {isIdentityReady ? '● Identity Ready' : '○ Identity Unconfirmed'}
          </div>
        </div>

        {approvalError && (
          <div
            data-testid="approval-error-banner"
            style={{
              fontSize: '12px',
              color: '#991b1b',
              background: '#fee2e2',
              padding: '6px 10px',
              borderRadius: 'var(--radius-sm)',
            }}
          >
            ✕ {approvalError}
          </div>
        )}

        {approvalSuccess && (
          <div
            data-testid="approval-success-banner"
            style={{
              fontSize: '12px',
              color: '#065f46',
              background: '#d1fae5',
              padding: '6px 10px',
              borderRadius: 'var(--radius-sm)',
            }}
          >
            ✓ Document approved! Eligible for FHIR export.
          </div>
        )}

        <div style={{ display: 'flex', gap: '8px' }}>
          <button
            data-testid="btn-approve-document"
            disabled={!allClaimsTerminal || !isIdentityReady || loading}
            onClick={handleApproveDocument}
            className="btn btn-primary"
            style={{
              flex: 1,
              opacity: !allClaimsTerminal || !isIdentityReady ? 0.5 : 1,
              cursor: !allClaimsTerminal || !isIdentityReady ? 'not-allowed' : 'pointer',
            }}
            title={
              !allClaimsTerminal
                ? 'All claims must be in terminal review states (ACCEPTED, EDITED, REJECTED) before approval.'
                : !isIdentityReady
                ? 'Patient identity must be confirmed or mismatch accepted before approval.'
                : 'Execute final document approval and mark as FHIR export eligible.'
            }
          >
            ★ Approve Document (Four-Eyes)
          </button>
        </div>
      </div>
    </div>
  );
};
