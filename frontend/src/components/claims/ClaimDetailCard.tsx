/**
 * ClaimDetailCard Component — Inspection, Verification Breakdown & Review Actions
 * 
 * Authority:
 *   - PRODUCTIZATION_SPEC.md §7.3, §13.1
 *   - PROJECT_SPEC.md §13 (Automation-bias mitigations & confidence display rules)
 */

import React, { useState } from 'react';
import type {
  ClaimActionPayload,
  DocumentClaimItem,
  IdentityVerificationStatus,
} from '../../types';

export interface ClaimDetailCardProps {
  claim: DocumentClaimItem;
  identityStatus?: IdentityVerificationStatus;
  userRole?: string;
  onAction: (payload: ClaimActionPayload) => Promise<void>;
  loading?: boolean;
}

export const VERDICT_MAP: Record<string, { label: string; badgeClass: string; description: string }> = {
  SUPPORTED: {
    label: 'Matches source',
    badgeClass: 'badge-supported',
    description: 'Extracted claim matches verbatim source text in the document.',
  },
  DISCREPANCY: {
    label: 'Differs from source (value/unit/dose/...)',
    badgeClass: 'badge-discrepancy',
    description: 'Extracted claim conflicts with verbatim source text in the document.',
  },
  INSUFFICIENT_EVIDENCE: {
    label: 'Not found in source',
    badgeClass: 'badge-unverified',
    description: 'No grounding evidence could substantiate the extracted claim.',
  },
};

const DISCLAIMER_TEXT =
  'This checks the extracted data against this document only. It does not check whether the document or the value is clinically correct.';

const EDIT_REASONS = [
  { code: 'TYPO_TRANSCRIPTION', label: 'Transcription / OCR Typo' },
  { code: 'DOSAGE_CORRECTION', label: 'Dosage or Strength Correction' },
  { code: 'UNIT_STANDARDIZATION', label: 'Unit / Measurement Correction' },
  { code: 'TEMPORAL_CORRECTION', label: 'Temporal / Historical Correction' },
  { code: 'OMISSION_RECTIFIED', label: 'Missing Detail Rectified' },
  { code: 'CLINICAL_OVERRIDE', label: 'Clinical Documentation Override' },
  { code: 'OTHER_AUDITED', label: 'Other (Audited Justification)' },
];

const REJECT_REASONS = [
  { code: 'HALLUCINATION', label: 'Hallucination / Not in Source' },
  { code: 'INCORRECT_EXTRACTION', label: 'Incorrect Extraction / Wrong Span' },
  { code: 'DOCUMENT_UNREADABLE', label: 'Document Unreadable / Degraded OCR' },
  { code: 'NEGATED_CONDITION', label: 'Condition Negated in Source' },
  { code: 'DUPLICATE_CLAIM', label: 'Duplicate Claim' },
  { code: 'PATIENT_IDENTITY_MISMATCH', label: 'Extracted from Different Patient Section' },
  { code: 'OTHER_AUDITED', label: 'Other (Audited Justification)' },
];

export const ClaimDetailCard: React.FC<ClaimDetailCardProps> = ({
  claim,
  identityStatus,
  onAction,
  loading = false,
}) => {
  const [activeMode, setActiveMode] = useState<'VIEW' | 'EDIT' | 'REJECT' | 'ESCALATE'>('VIEW');
  const [editValue, setEditValue] = useState<string>(claim.value);
  const [editReason, setEditReason] = useState<string>('');
  const [editNotes, setEditNotes] = useState<string>('');
  const [rejectReason, setRejectReason] = useState<string>('');
  const [rejectNotes, setRejectNotes] = useState<string>('');
  const [escalateNotes, setEscalateNotes] = useState<string>('');
  const [actionError, setActionError] = useState<string | null>(null);

  const rawVerdict = (claim.verification?.label || '').toUpperCase();
  const verdictInfo = VERDICT_MAP[rawVerdict] || {
    label: rawVerdict || 'Unverified',
    badgeClass: 'badge-unverified',
    description: 'Verification status pending or unrecognized.',
  };

  const isStageC = (claim.verification?.stage || '').toUpperCase() === 'C';
  const stage = (claim.verification?.stage || 'A').toUpperCase();
  const hasCalibratedConfidence =
    !isStageC &&
    claim.verification?.calibrated_confidence !== undefined &&
    claim.verification?.calibrated_confidence !== null;

  // Determine applicable flags
  const flags: { key: string; label: string; detail: string }[] = [];

  const ocrConf = claim.grounding?.ocr_confidence;
  if (ocrConf !== undefined && ocrConf !== null && ocrConf < 0.85) {
    flags.push({
      key: 'low-ocr',
      label: 'Low OCR Confidence',
      detail: `OCR confidence is ${(ocrConf * 100).toFixed(1)}% (< 85% threshold)`,
    });
  }

  const contradiction = claim.verification?.contradiction_type;
  if (contradiction && contradiction.toLowerCase().includes('injection')) {
    flags.push({
      key: 'injection',
      label: 'Injection Pattern Flag',
      detail: 'Adversarial instruction or prompt injection pattern detected in source.',
    });
  }

  if (claim.verification?.is_fallback) {
    flags.push({
      key: 'fallback',
      label: 'Fallback Active',
      detail: 'Extraction or verification used fallback heuristics.',
    });
  }

  if (identityStatus === 'MISMATCH' || identityStatus === 'UNVERIFIED') {
    flags.push({
      key: 'identity-mismatch',
      label: 'Patient Identity Unverified',
      detail: 'Document identity is not yet confirmed or has an unaccepted demographic mismatch.',
    });
  }

  // Action handlers
  const handleAccept = async () => {
    setActionError(null);
    try {
      await onAction({
        action: 'ACCEPT',
        expected_version: claim.version,
      });
      setActiveMode('VIEW');
    } catch (err: any) {
      setActionError(err.message || 'Failed to accept claim');
    }
  };

  const handleEditSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setActionError(null);

    if (!editValue.trim()) {
      setActionError('New claim value cannot be empty.');
      return;
    }
    if (!editReason.trim()) {
      setActionError('Reason code is required for editing a claim.');
      return;
    }

    try {
      await onAction({
        action: 'EDIT',
        expected_version: claim.version,
        new_value: editValue.trim(),
        reason_code: editReason,
        notes: editNotes.trim() || undefined,
      });
      setActiveMode('VIEW');
    } catch (err: any) {
      setActionError(err.message || 'Failed to submit edit');
    }
  };

  const handleRejectSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setActionError(null);

    if (!rejectReason.trim()) {
      setActionError('Reason code is required to reject a claim.');
      return;
    }

    try {
      await onAction({
        action: 'REJECT',
        expected_version: claim.version,
        reason_code: rejectReason,
        notes: rejectNotes.trim() || undefined,
      });
      setActiveMode('VIEW');
    } catch (err: any) {
      setActionError(err.message || 'Failed to reject claim');
    }
  };

  const handleEscalateSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setActionError(null);

    try {
      await onAction({
        action: 'ESCALATE',
        expected_version: claim.version,
        notes: escalateNotes.trim() || undefined,
      });
      setActiveMode('VIEW');
    } catch (err: any) {
      setActionError(err.message || 'Failed to escalate claim');
    }
  };

  const handleReopen = async () => {
    setActionError(null);
    try {
      await onAction({
        action: 'REOPEN',
        expected_version: claim.version,
      });
      setActiveMode('VIEW');
    } catch (err: any) {
      setActionError(err.message || 'Failed to reopen claim');
    }
  };

  return (
    <div
      data-testid="claim-detail-card"
      style={{
        background: 'var(--color-surface)',
        border: '1px solid var(--color-border)',
        borderRadius: 'var(--radius-md)',
        padding: '20px',
        display: 'flex',
        flexDirection: 'column',
        gap: '16px',
      }}
    >
      {/* ─── HEADER: FIELD & REVIEW STATE ─────────────────────── */}
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', gap: '8px' }}>
        <div>
          <div style={{ fontSize: '12px', textTransform: 'uppercase', color: 'var(--color-text-muted)', fontWeight: 600 }}>
            {claim.group_id ? `${claim.group_id} • ` : ''}Claim Field
          </div>
          <h3
            data-testid="claim-field-type"
            style={{ margin: '2px 0 0', fontSize: '18px', fontWeight: 700, color: 'var(--color-text)' }}
          >
            {claim.field_type}
          </h3>
        </div>

        <div style={{ display: 'flex', gap: '6px', alignItems: 'center' }}>
          <span
            data-testid="claim-review-state-badge"
            className={`badge badge-${
              claim.review_state === 'ACCEPTED'
                ? 'supported'
                : claim.review_state === 'REJECTED'
                ? 'discrepancy'
                : claim.review_state === 'ESCALATED'
                ? 'mismatch'
                : 'unverified'
            }`}
          >
            {claim.review_state}
          </span>
          <span style={{ fontSize: '11px', color: 'var(--color-text-muted)', fontFamily: 'monospace' }}>
            v{claim.version} ({claim.origin})
          </span>
        </div>
      </div>

      {/* ─── VALUE & NORMALIZED CONCEPT ───────────────────────── */}
      <div
        style={{
          background: 'var(--color-bg)',
          border: '1px solid var(--color-border)',
          borderRadius: 'var(--radius-sm)',
          padding: '12px',
        }}
      >
        <div style={{ fontSize: '11px', color: 'var(--color-text-muted)', fontWeight: 600, marginBottom: '2px' }}>
          EXTRACTED RAW VALUE
        </div>
        <div data-testid="claim-raw-value" style={{ fontSize: '15px', fontWeight: 600, color: 'var(--color-text)' }}>
          {claim.value}
        </div>

        {claim.normalized_code && (
          <div style={{ marginTop: '8px', paddingTop: '8px', borderTop: '1px dashed var(--color-border)', fontSize: '12px' }}>
            <span style={{ color: 'var(--color-text-muted)' }}>Normalized Concept: </span>
            <code style={{ fontWeight: 600, color: 'var(--color-primary)' }}>
              {claim.normalized_code}
            </code>
            <span style={{ color: 'var(--color-text-muted)' }}> ({claim.code_system || 'Standard'})</span>
          </div>
        )}
      </div>

      {/* ─── PLAIN-LANGUAGE VERDICT ─────────────────────────────── */}
      <div
        data-testid="verdict-banner"
        style={{
          display: 'flex',
          flexDirection: 'column',
          gap: '6px',
          padding: '12px',
          background: 'var(--color-bg)',
          borderLeft: `4px solid ${
            rawVerdict === 'SUPPORTED' ? '#059669' : rawVerdict === 'DISCREPANCY' ? '#d97706' : '#ca8a04'
          }`,
          borderRadius: 'var(--radius-sm)',
        }}
      >
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
          <span style={{ fontSize: '11px', fontWeight: 700, textTransform: 'uppercase', color: 'var(--color-text-muted)' }}>
            Verification Verdict
          </span>
          <span data-testid="verdict-badge" className={`badge ${verdictInfo.badgeClass}`}>
            {verdictInfo.label}
          </span>
        </div>
        <div style={{ fontSize: '13px', color: 'var(--color-text)' }}>
          {verdictInfo.description}
        </div>

        {/* Required Disclaimer */}
        <div
          data-testid="verification-disclaimer"
          style={{
            fontSize: '11px',
            color: 'var(--color-text-muted)',
            fontStyle: 'italic',
            marginTop: '4px',
            borderTop: '1px solid var(--color-border)',
            paddingTop: '4px',
          }}
          title={DISCLAIMER_TEXT}
        >
          ℹ {DISCLAIMER_TEXT}
        </div>
      </div>

      {/* ─── CONFIDENCE SPECIFICATION RULES (§13.1) ───────────── */}
      <div data-testid="confidence-section" style={{ fontSize: '13px' }}>
        {isStageC ? (
          /* Stage C: MUST NOT present calibrated percentage! */
          <div
            data-testid="stage-c-confidence-banner"
            style={{
              padding: '10px 12px',
              background: '#fef3c7',
              border: '1px solid #fde68a',
              borderRadius: 'var(--radius-sm)',
              color: '#92400e',
            }}
          >
            <div style={{ fontWeight: 600 }}>Human Review Required (Stage C)</div>
            <div style={{ fontSize: '12px', marginTop: '2px' }}>
              Second-check performed:{' '}
              <strong>{claim.verification?.second_check_performed ? 'Yes' : 'No'}</strong>
            </div>
            {claim.verification?.rationale && (
              <div style={{ fontSize: '12px', marginTop: '4px', fontStyle: 'italic' }}>
                Rationale: "{claim.verification.rationale}"
              </div>
            )}
          </div>
        ) : hasCalibratedConfidence ? (
          /* Stage A/B: display calibrated Stage B probability */
          <div data-testid="stage-ab-confidence-banner" style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
            <span style={{ color: 'var(--color-text-muted)' }}>Calibrated Confidence:</span>
            <strong data-testid="calibrated-percentage-display" style={{ color: 'var(--color-text)' }}>
              {((claim.verification?.calibrated_confidence || 0) * 100).toFixed(1)}%
            </strong>
            <span style={{ fontSize: '11px', color: 'var(--color-text-muted)' }}>
              (Stage {stage} Cascade)
            </span>
          </div>
        ) : (
          <div style={{ color: 'var(--color-text-muted)', fontStyle: 'italic', fontSize: '12px' }}>
            Confidence calibration not applicable for this extraction tier.
          </div>
        )}
      </div>

      {/* ─── APPLICABLE FLAGS ─────────────────────────────────── */}
      {flags.length > 0 && (
        <div data-testid="claim-flags-container" style={{ display: 'flex', flexDirection: 'column', gap: '6px' }}>
          <div style={{ fontSize: '11px', fontWeight: 700, textTransform: 'uppercase', color: 'var(--color-text-muted)' }}>
            Applicable Flags ({flags.length})
          </div>
          <div style={{ display: 'flex', flexWrap: 'wrap', gap: '6px' }}>
            {flags.map((f) => (
              <div
                key={f.key}
                data-testid={`flag-${f.key}`}
                style={{
                  display: 'flex',
                  alignItems: 'center',
                  gap: '6px',
                  padding: '4px 10px',
                  background: '#fee2e2',
                  border: '1px solid #fca5a5',
                  borderRadius: 'var(--radius-sm)',
                  color: '#991b1b',
                  fontSize: '11px',
                  fontWeight: 600,
                }}
                title={f.detail}
              >
                <span>⚠</span>
                <span>{f.label}</span>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* ─── EXPANDABLE "HOW IT WAS CHECKED" ───────────────────── */}
      <details
        data-testid="how-it-was-checked-accordion"
        style={{
          background: 'var(--color-bg)',
          border: '1px solid var(--color-border)',
          borderRadius: 'var(--radius-sm)',
          padding: '8px 12px',
          fontSize: '12px',
        }}
      >
        <summary style={{ cursor: 'pointer', fontWeight: 600, color: 'var(--color-primary)' }}>
          🔍 How It Was Checked
        </summary>
        <div style={{ marginTop: '8px', display: 'flex', flexDirection: 'column', gap: '4px', color: 'var(--color-text)' }}>
          <div>
            <strong>Cascade Stages Reached:</strong> Stage {stage}
          </div>
          <div>
            <strong>Automated Second Check:</strong>{' '}
            {claim.verification?.second_check_performed ? 'Used' : 'Not required or unavailable'}
          </div>
          <div>
            <strong>Configuration Status:</strong>{' '}
            {claim.verification?.config_status || 'Qualified Production Profile'}
          </div>
          {claim.evidence_id && (
            <div>
              <strong>Grounding Span:</strong> Evidence ID <code style={{ fontSize: '11px' }}>{claim.evidence_id}</code>
            </div>
          )}
        </div>
      </details>

      {/* ─── ACTION ERROR FEEDBACK ─────────────────────────────── */}
      {actionError && (
        <div
          data-testid="claim-action-error"
          style={{
            padding: '10px 12px',
            background: '#fee2e2',
            border: '1px solid #fca5a5',
            borderRadius: 'var(--radius-sm)',
            color: '#991b1b',
            fontSize: '13px',
          }}
        >
          ✕ {actionError}
        </div>
      )}

      {/* ─── REVIEW ACTIONS STATE MACHINE ─────────────────────── */}
      <div style={{ marginTop: 'auto', paddingTop: '12px', borderTop: '1px solid var(--color-border)' }}>
        {activeMode === 'VIEW' && (
          <div style={{ display: 'flex', flexWrap: 'wrap', gap: '8px' }}>
            {claim.review_state === 'PENDING' ? (
              <>
                <button
                  data-testid="btn-action-accept"
                  disabled={loading}
                  onClick={handleAccept}
                  className="btn btn-primary"
                  style={{ flex: 1, minWidth: '90px' }}
                >
                  ✓ Accept
                </button>
                <button
                  data-testid="btn-action-edit"
                  disabled={loading}
                  onClick={() => setActiveMode('EDIT')}
                  className="btn"
                  style={{
                    background: 'var(--color-surface)',
                    border: '1px solid var(--color-border)',
                    flex: 1,
                    minWidth: '90px',
                  }}
                >
                  ✎ Edit
                </button>
                <button
                  data-testid="btn-action-reject"
                  disabled={loading}
                  onClick={() => setActiveMode('REJECT')}
                  className="btn"
                  style={{
                    background: 'var(--color-surface)',
                    border: '1px solid #fca5a5',
                    color: '#dc2626',
                    flex: 1,
                    minWidth: '90px',
                  }}
                >
                  ✕ Reject
                </button>
                <button
                  data-testid="btn-action-escalate"
                  disabled={loading}
                  onClick={() => setActiveMode('ESCALATE')}
                  className="btn"
                  style={{
                    background: 'var(--color-surface)',
                    border: '1px solid #fde68a',
                    color: '#d97706',
                    flex: 1,
                    minWidth: '90px',
                  }}
                >
                  ⚑ Escalate
                </button>
              </>
            ) : (
              <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', width: '100%' }}>
                <span style={{ fontSize: '13px', color: 'var(--color-text-muted)' }}>
                  Decision recorded as <strong>{claim.review_state}</strong>.
                </span>
                <button
                  data-testid="btn-action-reopen"
                  disabled={loading}
                  onClick={handleReopen}
                  className="btn"
                  style={{
                    background: 'var(--color-surface)',
                    border: '1px solid var(--color-border)',
                    fontSize: '12px',
                    padding: '4px 10px',
                  }}
                >
                  ↺ Reopen Claim
                </button>
              </div>
            )}
          </div>
        )}

        {/* ─── INLINE EDIT FORM ───────────────────────────────── */}
        {activeMode === 'EDIT' && (
          <form data-testid="edit-claim-form" noValidate onSubmit={handleEditSubmit} style={{ display: 'flex', flexDirection: 'column', gap: '10px' }}>
            <div style={{ fontWeight: 600, fontSize: '14px' }}>Edit Claim Value (Inline Re-verification)</div>
            <div>
              <label style={{ display: 'block', fontSize: '11px', fontWeight: 600, marginBottom: '2px' }}>
                New Value *
              </label>
              <input
                data-testid="edit-claim-value-input"
                type="text"
                value={editValue}
                onChange={(e) => setEditValue(e.target.value)}
                style={{
                  width: '100%',
                  padding: '6px 8px',
                  border: '1px solid var(--color-border)',
                  borderRadius: 'var(--radius-sm)',
                  fontSize: '13px',
                }}
                required
              />
            </div>
            <div>
              <label style={{ display: 'block', fontSize: '11px', fontWeight: 600, marginBottom: '2px' }}>
                Reason Code *
              </label>
              <select
                data-testid="edit-reason-code-select"
                value={editReason}
                onChange={(e) => setEditReason(e.target.value)}
                style={{
                  width: '100%',
                  padding: '6px 8px',
                  border: '1px solid var(--color-border)',
                  borderRadius: 'var(--radius-sm)',
                  fontSize: '13px',
                }}
                required
              >
                <option value="">-- Select Edit Justification --</option>
                {EDIT_REASONS.map((r) => (
                  <option key={r.code} value={r.code}>
                    {r.label}
                  </option>
                ))}
              </select>
            </div>
            <div>
              <label style={{ display: 'block', fontSize: '11px', fontWeight: 600, marginBottom: '2px' }}>
                Reviewer Notes (Optional)
              </label>
              <input
                data-testid="edit-notes-input"
                type="text"
                value={editNotes}
                onChange={(e) => setEditNotes(e.target.value)}
                placeholder="Audit notes..."
                style={{
                  width: '100%',
                  padding: '6px 8px',
                  border: '1px solid var(--color-border)',
                  borderRadius: 'var(--radius-sm)',
                  fontSize: '13px',
                }}
              />
            </div>
            <div style={{ display: 'flex', gap: '8px', justifyContent: 'flex-end', marginTop: '4px' }}>
              <button
                type="button"
                onClick={() => setActiveMode('VIEW')}
                className="btn"
                style={{ background: 'var(--color-surface)', border: '1px solid var(--color-border)' }}
              >
                Cancel
              </button>
              <button
                data-testid="btn-submit-edit"
                type="submit"
                disabled={loading}
                className="btn btn-primary"
              >
                {loading ? 'Re-verifying...' : 'Save & Re-verify'}
              </button>
            </div>
          </form>
        )}

        {/* ─── REJECT FORM ────────────────────────────────────── */}
        {activeMode === 'REJECT' && (
          <form data-testid="reject-claim-form" noValidate onSubmit={handleRejectSubmit} style={{ display: 'flex', flexDirection: 'column', gap: '10px' }}>
            <div style={{ fontWeight: 600, fontSize: '14px', color: '#dc2626' }}>Reject Clinical Claim</div>
            <div>
              <label style={{ display: 'block', fontSize: '11px', fontWeight: 600, marginBottom: '2px' }}>
                Reason Code *
              </label>
              <select
                data-testid="reject-reason-code-select"
                value={rejectReason}
                onChange={(e) => setRejectReason(e.target.value)}
                style={{
                  width: '100%',
                  padding: '6px 8px',
                  border: '1px solid var(--color-border)',
                  borderRadius: 'var(--radius-sm)',
                  fontSize: '13px',
                }}
                required
              >
                <option value="">-- Select Rejection Reason --</option>
                {REJECT_REASONS.map((r) => (
                  <option key={r.code} value={r.code}>
                    {r.label}
                  </option>
                ))}
              </select>
            </div>
            <div>
              <label style={{ display: 'block', fontSize: '11px', fontWeight: 600, marginBottom: '2px' }}>
                Reviewer Notes (Optional)
              </label>
              <input
                data-testid="reject-notes-input"
                type="text"
                value={rejectNotes}
                onChange={(e) => setRejectNotes(e.target.value)}
                placeholder="Audit notes..."
                style={{
                  width: '100%',
                  padding: '6px 8px',
                  border: '1px solid var(--color-border)',
                  borderRadius: 'var(--radius-sm)',
                  fontSize: '13px',
                }}
              />
            </div>
            <div style={{ display: 'flex', gap: '8px', justifyContent: 'flex-end', marginTop: '4px' }}>
              <button
                type="button"
                onClick={() => setActiveMode('VIEW')}
                className="btn"
                style={{ background: 'var(--color-surface)', border: '1px solid var(--color-border)' }}
              >
                Cancel
              </button>
              <button
                data-testid="btn-submit-reject"
                type="submit"
                disabled={loading}
                className="btn"
                style={{ background: '#dc2626', color: '#fff', border: 'none' }}
              >
                {loading ? 'Rejecting...' : 'Confirm Rejection'}
              </button>
            </div>
          </form>
        )}

        {/* ─── ESCALATE FORM ──────────────────────────────────── */}
        {activeMode === 'ESCALATE' && (
          <form data-testid="escalate-claim-form" onSubmit={handleEscalateSubmit} style={{ display: 'flex', flexDirection: 'column', gap: '10px' }}>
            <div style={{ fontWeight: 600, fontSize: '14px', color: '#d97706' }}>Escalate for Senior Review</div>
            <div>
              <label style={{ display: 'block', fontSize: '11px', fontWeight: 600, marginBottom: '2px' }}>
                Escalation Notes (Optional)
              </label>
              <input
                data-testid="escalate-notes-input"
                type="text"
                value={escalateNotes}
                onChange={(e) => setEscalateNotes(e.target.value)}
                placeholder="Reason for clinical escalation..."
                style={{
                  width: '100%',
                  padding: '6px 8px',
                  border: '1px solid var(--color-border)',
                  borderRadius: 'var(--radius-sm)',
                  fontSize: '13px',
                }}
              />
            </div>
            <div style={{ display: 'flex', gap: '8px', justifyContent: 'flex-end', marginTop: '4px' }}>
              <button
                type="button"
                onClick={() => setActiveMode('VIEW')}
                className="btn"
                style={{ background: 'var(--color-surface)', border: '1px solid var(--color-border)' }}
              >
                Cancel
              </button>
              <button
                data-testid="btn-submit-escalate"
                type="submit"
                disabled={loading}
                className="btn"
                style={{ background: '#d97706', color: '#fff', border: 'none' }}
              >
                {loading ? 'Escalating...' : 'Confirm Escalation'}
              </button>
            </div>
          </form>
        )}
      </div>
    </div>
  );
};
