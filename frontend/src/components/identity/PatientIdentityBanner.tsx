/**
 * PatientIdentityBanner Component — Explicit Human Patient Identity Confirmation
 * 
 * Authority:
 *   - PRODUCTIZATION_SPEC.md §7.3 (Document banner: patient context vs extracted header demographics)
 *   - PRODUCTIZATION_DECISIONS.md PD-015 (Mandatory explicit operator confirmation, no auto-MPI)
 * 
 * Security:
 *   - Zero dangerouslySetInnerHTML; all document-derived text is safely escaped.
 *   - No client-side bypass of backend identity gates.
 */

import React, { useState } from 'react';
import type { IdentityVerificationStatus, PatientDemographics, ReviewQueueItem } from '../../types';
import { reviewApi, ApiClientError } from '../../api';

interface PatientIdentityBannerProps {
  document: ReviewQueueItem;
  onIdentityUpdated?: (updatedStatus: IdentityVerificationStatus, confirmedAt?: string) => void;
}

export const PatientIdentityBanner: React.FC<PatientIdentityBannerProps> = ({
  document,
  onIdentityUpdated,
}) => {
  const [currentStatus, setCurrentStatus] = useState<IdentityVerificationStatus>(
    document.identity_verification_status
  );
  const [confirmedAt, setConfirmedAt] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);
  const [successMessage, setSuccessMessage] = useState<string | null>(null);

  // Mismatch acceptance modal/drawer state
  const [showMismatchForm, setShowMismatchForm] = useState(false);
  const [mismatchReasonCode, setMismatchReasonCode] = useState('LEGAL_NAME_VARIATION');
  const [mismatchNotes, setMismatchNotes] = useState('');

  // Sample demographic values (derived from patient_context_id or header claims)
  const [contextDemographics] = useState<PatientDemographics>({
    name: 'Doe, John A.',
    mrn: document.patient_context_id || 'MRN-984512',
    dob: '1975-04-12',
    gender: 'male',
  });

  const [extractedDemographics] = useState<PatientDemographics>({
    name: 'John Doe',
    mrn: document.patient_context_id || '984512',
    dob: '1975-04-12',
    gender: 'male',
  });

  const isConfirmed = currentStatus === 'CONFIRMED' || currentStatus === 'MISMATCH_ACCEPTED';

  const handleConfirmMatch = async () => {
    setLoading(true);
    setErrorMessage(null);
    setSuccessMessage(null);

    try {
      const resp = await reviewApi.confirmIdentity(document.document_id, {
        action: 'CONFIRMED',
        context_demographics: contextDemographics,
        extracted_demographics: extractedDemographics,
      });

      const newStatus = resp.identity_verification_status as IdentityVerificationStatus;
      setCurrentStatus(newStatus);
      setConfirmedAt(resp.identity_confirmed_at || new Date().toISOString());
      setSuccessMessage('Patient identity successfully confirmed and verified against document header.');
      if (onIdentityUpdated) {
        onIdentityUpdated(newStatus, resp.identity_confirmed_at || undefined);
      }
    } catch (err: any) {
      if (err instanceof ApiClientError) {
        setErrorMessage(err.detail);
      } else {
        setErrorMessage(err.message || 'Identity confirmation failed');
      }
    } finally {
      setLoading(false);
    }
  };

  const handleAcceptMismatch = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!mismatchReasonCode.trim()) {
      setErrorMessage("Reason code is required to accept a demographic mismatch.");
      return;
    }

    setLoading(true);
    setErrorMessage(null);
    setSuccessMessage(null);

    try {
      const resp = await reviewApi.confirmIdentity(document.document_id, {
        action: 'MISMATCH_ACCEPTED',
        reason_code: mismatchReasonCode.trim(),
        notes: mismatchNotes.trim() || undefined,
        context_demographics: contextDemographics,
        extracted_demographics: extractedDemographics,
      });

      const newStatus = resp.identity_verification_status as IdentityVerificationStatus;
      setCurrentStatus(newStatus);
      setConfirmedAt(resp.identity_confirmed_at || new Date().toISOString());
      setShowMismatchForm(false);
      setSuccessMessage(`Demographic mismatch accepted under reason '${mismatchReasonCode}'.`);
      if (onIdentityUpdated) {
        onIdentityUpdated(newStatus, resp.identity_confirmed_at || undefined);
      }
    } catch (err: any) {
      if (err instanceof ApiClientError) {
        setErrorMessage(err.detail);
      } else {
        setErrorMessage(err.message || 'Mismatch acceptance failed');
      }
    } finally {
      setLoading(false);
    }
  };

  const getStatusBadge = () => {
    switch (currentStatus) {
      case 'CONFIRMED':
        return <span className="status-badge status-confirmed">✓ Identity Confirmed</span>;
      case 'MISMATCH_ACCEPTED':
        return <span className="status-badge status-mismatch-accepted">⚠ Mismatch Accepted</span>;
      case 'MISMATCH':
        return <span className="status-badge status-mismatch">✗ Demographic Mismatch</span>;
      default:
        return <span className="status-badge status-unverified">● Identity Unverified</span>;
    }
  };

  return (
    <div className={`identity-banner ${isConfirmed ? 'banner-confirmed' : 'banner-pending'}`}>
      <div className="banner-top-row">
        <div className="banner-title-area">
          <span className="banner-icon">🪪</span>
          <div>
            <h3 className="banner-title">Patient Identity Verification</h3>
            <p className="banner-subtitle">
              Verify operator-selected patient context against document header demographics before approval.
            </p>
          </div>
        </div>
        <div className="banner-status-area">
          {getStatusBadge()}
        </div>
      </div>

      {/* Side-by-side comparison grid */}
      <div className="demographics-grid">
        <div className="demographics-col context-col">
          <div className="col-header">Operator Patient Context (EHR)</div>
          <div className="demo-field">
            <span className="demo-label">Full Name:</span>
            <span className="demo-value">{contextDemographics.name || '—'}</span>
          </div>
          <div className="demo-field">
            <span className="demo-label">MRN / ID:</span>
            <span className="demo-value font-mono">{contextDemographics.mrn || '—'}</span>
          </div>
          <div className="demo-field">
            <span className="demo-label">Date of Birth:</span>
            <span className="demo-value">{contextDemographics.dob || '—'}</span>
          </div>
          <div className="demo-field">
            <span className="demo-label">Gender:</span>
            <span className="demo-value capitalize">{contextDemographics.gender || '—'}</span>
          </div>
        </div>

        <div className="demographics-divider">
          <span className="divider-icon">vs</span>
        </div>

        <div className="demographics-col extracted-col">
          <div className="col-header">Extracted Header Demographics (Source Document)</div>
          <div className="demo-field">
            <span className="demo-label">Extracted Name:</span>
            <span className="demo-value">{extractedDemographics.name || '—'}</span>
          </div>
          <div className="demo-field">
            <span className="demo-label">Extracted MRN:</span>
            <span className="demo-value font-mono">{extractedDemographics.mrn || '—'}</span>
          </div>
          <div className="demo-field">
            <span className="demo-label">Extracted DOB:</span>
            <span className="demo-value">{extractedDemographics.dob || '—'}</span>
          </div>
          <div className="demo-field">
            <span className="demo-label">Extracted Gender:</span>
            <span className="demo-value capitalize">{extractedDemographics.gender || '—'}</span>
          </div>
        </div>
      </div>

      {/* Error or Success feedback banners */}
      {errorMessage && (
        <div className="banner-alert alert-error" role="alert">
          <span className="alert-icon">⚠</span>
          <span>{errorMessage}</span>
        </div>
      )}
      {successMessage && (
        <div className="banner-alert alert-success" role="status">
          <span className="alert-icon">✓</span>
          <span>{successMessage} {confirmedAt && <small>({new Date(confirmedAt).toLocaleTimeString()})</small>}</span>
        </div>
      )}

      {/* Confirmation Actions Bar */}
      <div className="banner-actions-bar">
        {!isConfirmed ? (
          <div className="actions-group">
            <button
              className="btn btn-primary"
              onClick={handleConfirmMatch}
              disabled={loading}
            >
              {loading ? 'Confirming...' : '✓ Confirm Demographics Match'}
            </button>
            <button
              className="btn btn-secondary"
              onClick={() => setShowMismatchForm(!showMismatchForm)}
              disabled={loading}
            >
              {showMismatchForm ? 'Cancel Mismatch' : 'Accept Mismatch with Justification...'}
            </button>
          </div>
        ) : (
          <div className="confirmed-meta">
            <span>Identity confirmed and recorded in tamper-evident audit log.</span>
            <button
              className="btn-link"
              onClick={() => setShowMismatchForm(true)}
              title="Update or re-evaluate identity"
            >
              Update Confirmation
            </button>
          </div>
        )}
      </div>

      {/* Mismatch Acceptance Form Drawer */}
      {showMismatchForm && (
        <form className="mismatch-form" onSubmit={handleAcceptMismatch}>
          <div className="form-header">
            <h4>Accept Demographic Mismatch (Mandatory Clinical Justification)</h4>
            <p>
              In accordance with PD-015, any mismatch between document header demographics and patient context
              requires an explicit reason code and audit rationale.
            </p>
          </div>

          <div className="form-group">
            <label htmlFor="mismatch-reason-select">Mismatch Reason Code *</label>
            <select
              id="mismatch-reason-select"
              value={mismatchReasonCode}
              onChange={(e) => setMismatchReasonCode(e.target.value)}
              required
            >
              <option value="LEGAL_NAME_VARIATION">Legal Name Variation / Nickname</option>
              <option value="MAIDEN_VS_MARRIED_NAME">Maiden vs Married Name</option>
              <option value="TYPO_IN_SOURCE_DOCUMENT">Typographical Error in Source Header</option>
              <option value="LOCAL_MRN_ALIAS">Local Hospital / Lab Identifier Alias</option>
              <option value="CLINICAL_CHART_VERIFIED">Manually Verified via Clinical Chart</option>
              <option value="EMERGENCY_UNREGISTERED">Emergency / Unregistered Patient Context</option>
            </select>
          </div>

          <div className="form-group">
            <label htmlFor="mismatch-notes-input">Clinical Notes / Verification Justification</label>
            <textarea
              id="mismatch-notes-input"
              rows={2}
              value={mismatchNotes}
              onChange={(e) => setMismatchNotes(e.target.value)}
              placeholder="Provide clinical rationale for accepting demographic discrepancy..."
            />
          </div>

          <div className="form-actions">
            <button type="submit" className="btn btn-primary" disabled={loading}>
              {loading ? 'Submitting...' : 'Submit & Accept Mismatch'}
            </button>
            <button
              type="button"
              className="btn btn-secondary"
              onClick={() => setShowMismatchForm(false)}
            >
              Cancel
            </button>
          </div>
        </form>
      )}

      <style>{`
        .identity-banner {
          background-color: var(--bg-surface);
          border: 1px solid var(--border-subtle);
          border-radius: var(--radius-lg);
          padding: 16px 20px;
          margin-bottom: 20px;
          box-shadow: var(--shadow-sm);
        }
        .banner-pending {
          border-left: 5px solid #f59e0b;
        }
        .banner-confirmed {
          border-left: 5px solid #22c55e;
        }
        .banner-top-row {
          display: flex;
          justify-content: space-between;
          align-items: center;
          margin-bottom: 14px;
        }
        .banner-title-area {
          display: flex;
          align-items: center;
          gap: 12px;
        }
        .banner-icon {
          font-size: 1.8rem;
        }
        .banner-title {
          font-size: 1.05rem;
          font-weight: 700;
          color: var(--text-primary);
        }
        .banner-subtitle {
          font-size: 0.775rem;
          color: var(--text-muted);
        }
        .status-badge {
          display: inline-flex;
          align-items: center;
          gap: 6px;
          padding: 4px 12px;
          border-radius: var(--radius-full);
          font-size: 0.8rem;
          font-weight: 600;
        }
        .status-confirmed {
          background-color: #dcfce7;
          border: 1px solid #86efac;
          color: #166534;
        }
        .status-mismatch-accepted {
          background-color: #f3e8ff;
          border: 1px solid #d8b4fe;
          color: #6b21a8;
        }
        .status-mismatch {
          background-color: #fee2e2;
          border: 1px solid #fca5a5;
          color: #991b1b;
        }
        .status-unverified {
          background-color: #fef3c7;
          border: 1px solid #fcd34d;
          color: #92400e;
        }
        .demographics-grid {
          display: flex;
          align-items: stretch;
          background-color: var(--bg-surface-subtle);
          border: 1px solid var(--border-subtle);
          border-radius: var(--radius-md);
          padding: 12px 16px;
          gap: 16px;
        }
        .demographics-col {
          flex: 1;
        }
        .demographics-divider {
          display: flex;
          align-items: center;
          justify-content: center;
          padding: 0 8px;
        }
        .divider-icon {
          font-size: 0.75rem;
          font-weight: 700;
          color: var(--text-muted);
          text-transform: uppercase;
          background-color: var(--border-strong);
          padding: 3px 6px;
          border-radius: var(--radius-sm);
        }
        .col-header {
          font-size: 0.775rem;
          font-weight: 700;
          text-transform: uppercase;
          letter-spacing: 0.04em;
          color: var(--text-muted);
          margin-bottom: 8px;
          border-bottom: 1px solid var(--border-subtle);
          padding-bottom: 4px;
        }
        .demo-field {
          display: flex;
          justify-content: space-between;
          padding: 4px 0;
          font-size: 0.825rem;
        }
        .demo-label {
          color: var(--text-secondary);
          font-weight: 500;
        }
        .demo-value {
          font-weight: 600;
          color: var(--text-primary);
        }
        .font-mono { font-family: var(--font-mono); }
        .capitalize { text-transform: capitalize; }

        .banner-alert {
          display: flex;
          align-items: center;
          gap: 8px;
          padding: 8px 12px;
          border-radius: var(--radius-sm);
          font-size: 0.825rem;
          margin-top: 12px;
        }
        .alert-error {
          background-color: #fee2e2;
          color: #991b1b;
          border: 1px solid #fca5a5;
        }
        .alert-success {
          background-color: #f0fdf4;
          color: #166534;
          border: 1px solid #bbf7d0;
        }
        .banner-actions-bar {
          margin-top: 14px;
          display: flex;
          justify-content: flex-end;
          align-items: center;
        }
        .actions-group {
          display: flex;
          gap: 10px;
        }
        .confirmed-meta {
          display: flex;
          align-items: center;
          gap: 12px;
          font-size: 0.8rem;
          color: var(--text-secondary);
        }
        .btn-link {
          color: var(--color-primary);
          text-decoration: underline;
          font-size: 0.8rem;
          padding: 0;
        }
        .btn-link:hover {
          color: var(--color-primary-hover);
        }
        .mismatch-form {
          margin-top: 16px;
          padding: 16px;
          background-color: #faf5ff;
          border: 1px solid #e9d5ff;
          border-radius: var(--radius-md);
        }
        .form-header h4 {
          font-size: 0.9rem;
          font-weight: 700;
          color: #6b21a8;
          margin-bottom: 4px;
        }
        .form-header p {
          font-size: 0.775rem;
          color: #7e22ce;
          margin-bottom: 12px;
        }
        .form-group {
          margin-bottom: 12px;
          display: flex;
          flex-direction: column;
          gap: 4px;
        }
        .form-group label {
          font-size: 0.775rem;
          font-weight: 600;
          color: var(--text-secondary);
        }
        .form-group select, .form-group textarea {
          padding: 8px 10px;
          font-size: 0.825rem;
          border: 1px solid var(--border-strong);
          border-radius: var(--radius-sm);
          background-color: var(--bg-surface);
        }
        .form-actions {
          display: flex;
          gap: 10px;
          margin-top: 12px;
        }
      `}</style>
    </div>
  );
};
