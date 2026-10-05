/**
 * QueueView Component — Triage Review Queue Table (Phase 8.6 Step 3)
 * 
 * Authority:
 *   - PRODUCTIZATION_SPEC.md §7.2 (Triage review queue ordering and priority ranks)
 *   - PRODUCTIZATION_DECISIONS.md PD-002 (Mode 0: 100% human review)
 *   - PRODUCTIZATION_DECISIONS.md PD-013 (Review workbench SPA)
 * 
 * Security:
 *   - Zero dangerouslySetInnerHTML; all document strings and metadata are safely escaped.
 *   - Honors backend RBAC: displays authorization error if current role lacks queue permission.
 */

import React, { useEffect, useState, useCallback } from 'react';
import type { ReviewQueueItem } from '../../types';
import { reviewApi, ApiClientError } from '../../api';

interface QueueViewProps {
  onSelectDocument: (doc: ReviewQueueItem) => void;
}

export const QueueView: React.FC<QueueViewProps> = ({ onSelectDocument }) => {
  const [items, setItems] = useState<ReviewQueueItem[]>([]);
  const [totalCount, setTotalCount] = useState<number>(0);
  const [loading, setLoading] = useState<boolean>(true);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);
  const [correlationId, setCorrelationId] = useState<string | null>(null);
  const [statusFilter, setStatusFilter] = useState<string>('');

  const fetchQueue = useCallback(async () => {
    setLoading(true);
    setErrorMessage(null);
    setCorrelationId(null);

    try {
      const resp = await reviewApi.getQueue({
        status_filter: statusFilter || undefined,
        limit: 50,
      });
      // Sort deterministically by backend highest_risk_rank (Rank 1 highest risk) then uploaded_at
      const sorted = [...(resp.items || [])].sort((a, b) => {
        if (a.highest_risk_rank !== b.highest_risk_rank) {
          return a.highest_risk_rank - b.highest_risk_rank;
        }
        const timeA = a.uploaded_at ? new Date(a.uploaded_at).getTime() : 0;
        const timeB = b.uploaded_at ? new Date(b.uploaded_at).getTime() : 0;
        return timeA - timeB;
      });

      setItems(sorted);
      setTotalCount(resp.total ?? sorted.length);
    } catch (err: any) {
      if (err instanceof ApiClientError) {
        setErrorMessage(err.detail);
        setCorrelationId(err.correlationId || null);
      } else {
        setErrorMessage(err.message || 'Failed to load review queue');
      }
    } finally {
      setLoading(false);
    }
  }, [statusFilter]);

  useEffect(() => {
    fetchQueue();
  }, [fetchQueue]);

  const getPriorityBadge = (rank: number) => {
    if (rank <= 2) {
      return <span className="badge badge-discrepancy">Rank {rank} (High Risk)</span>;
    } else if (rank <= 6) {
      return <span className="badge badge-insufficient">Rank {rank} (Review Req.)</span>;
    } else {
      return <span className="badge badge-supported">Rank {rank} (Agreed)</span>;
    }
  };

  const getIdentityBadge = (idStatus: string) => {
    switch (idStatus) {
      case 'CONFIRMED':
        return <span className="status-dot-badge dot-green">✓ Confirmed</span>;
      case 'MISMATCH_ACCEPTED':
        return <span className="status-dot-badge dot-purple">⚠ Mismatch Accepted</span>;
      case 'MISMATCH':
        return <span className="status-dot-badge dot-red">✗ Mismatch</span>;
      default:
        return <span className="status-dot-badge dot-amber">● Unverified</span>;
    }
  };

  return (
    <div className="queue-container">
      <div className="queue-header-row">
        <div>
          <h2 className="queue-title">Triage Review Queue</h2>
          <p className="queue-subtitle">
            Clinical documents awaiting human verification under <strong>Mode 0 (100% Human Review)</strong>.
            Ordered by policy risk tier.
          </p>
        </div>

        <div className="queue-controls">
          <select
            className="filter-select"
            value={statusFilter}
            onChange={(e) => setStatusFilter(e.target.value)}
            disabled={loading}
            aria-label="Filter documents by status"
          >
            <option value="">All Reviewable Documents</option>
            <option value="PENDING_REVIEW">Pending Review Only</option>
            <option value="IN_REVIEW">In Review Only</option>
          </select>

          <button
            className="btn btn-secondary"
            onClick={fetchQueue}
            disabled={loading}
            title="Refresh queue items"
          >
            🔄 Refresh
          </button>
        </div>
      </div>

      {/* Loading State */}
      {loading && (
        <div className="card state-card" data-testid="queue-loading">
          <div className="spinner"></div>
          <p>Loading review queue from server...</p>
        </div>
      )}

      {/* Error State */}
      {!loading && errorMessage && (
        <div className="card state-card error-card" data-testid="queue-error">
          <div className="state-icon">⚠️</div>
          <h3>Failed to Load Review Queue</h3>
          <p className="error-text">{errorMessage}</p>
          {correlationId && (
            <p className="trace-text">
              Correlation ID: <code>{correlationId}</code>
            </p>
          )}
          <button className="btn btn-primary" onClick={fetchQueue}>
            Try Again
          </button>
        </div>
      )}

      {/* Empty State */}
      {!loading && !errorMessage && items.length === 0 && (
        <div className="card state-card empty-card" data-testid="queue-empty">
          <div className="state-icon">🎉</div>
          <h3>Review Queue is Empty</h3>
          <p>There are no clinical documents currently awaiting verification in this view.</p>
          <button className="btn btn-secondary" onClick={fetchQueue}>
            Refresh Status
          </button>
        </div>
      )}

      {/* Populated Queue Table */}
      {!loading && !errorMessage && items.length > 0 && (
        <div className="card table-card" data-testid="queue-table">
          <div className="table-meta-bar">
            <span>Showing {items.length} document{items.length === 1 ? '' : 's'} (Total: {totalCount})</span>
            <span className="mode-hint">Priority 1 (highest risk) displayed at the top</span>
          </div>

          <div className="table-responsive">
            <table className="queue-table">
              <thead>
                <tr>
                  <th>Triage Priority</th>
                  <th>Document Name / Reference</th>
                  <th>Ingested At</th>
                  <th>Patient Context</th>
                  <th>Identity Verification</th>
                  <th>Claims (Total / Discrepant)</th>
                  <th className="th-action">Action</th>
                </tr>
              </thead>
              <tbody>
                {items.map((doc) => (
                  <tr key={doc.document_id} className="queue-row" onClick={() => onSelectDocument(doc)}>
                    <td>{getPriorityBadge(doc.highest_risk_rank)}</td>
                    <td>
                      <div className="doc-name">{doc.original_filename}</div>
                      <div className="doc-id font-mono">ID: {doc.document_id.slice(0, 8)}...</div>
                    </td>
                    <td className="doc-date">
                      {doc.uploaded_at ? new Date(doc.uploaded_at).toLocaleString() : '—'}
                    </td>
                    <td>
                      {doc.patient_context_id ? (
                        <span className="patient-pill font-mono">{doc.patient_context_id}</span>
                      ) : (
                        <span className="text-muted">Not specified</span>
                      )}
                    </td>
                    <td>{getIdentityBadge(doc.identity_verification_status)}</td>
                    <td>
                      <div className="claims-stats">
                        <span className="total-claims">{doc.total_claims} claims</span>
                        {doc.discrepancy_count > 0 && (
                          <span className="discrepancy-pill">
                            {doc.discrepancy_count} discrepancy
                          </span>
                        )}
                        {doc.human_review_required_count > 0 && (
                          <span className="review-req-pill">
                            {doc.human_review_required_count} required
                          </span>
                        )}
                      </div>
                    </td>
                    <td className="td-action">
                      <button
                        className="btn btn-primary btn-sm"
                        onClick={(e) => {
                          e.stopPropagation();
                          onSelectDocument(doc);
                        }}
                      >
                        Open Workbench →
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      <style>{`
        .queue-container {
          max-width: 1300px;
          margin: 0 auto;
        }
        .queue-header-row {
          display: flex;
          justify-content: space-between;
          align-items: flex-end;
          margin-bottom: 20px;
        }
        .queue-title {
          font-size: 1.4rem;
          font-weight: 700;
          color: var(--text-primary);
        }
        .queue-subtitle {
          font-size: 0.875rem;
          color: var(--text-muted);
          margin-top: 4px;
        }
        .queue-controls {
          display: flex;
          gap: 10px;
          align-items: center;
        }
        .filter-select {
          padding: 7px 12px;
          font-size: 0.85rem;
          border: 1px solid var(--border-strong);
          border-radius: var(--radius-md);
          background-color: var(--bg-surface);
          color: var(--text-primary);
        }
        .state-card {
          text-align: center;
          padding: 60px 40px;
          display: flex;
          flex-direction: column;
          align-items: center;
          gap: 16px;
        }
        .state-icon {
          font-size: 2.8rem;
        }
        .error-card {
          border-left: 5px solid #ef4444;
        }
        .error-text {
          color: #991b1b;
          font-size: 0.95rem;
          max-width: 600px;
        }
        .trace-text {
          font-size: 0.8rem;
          color: var(--text-muted);
        }
        .table-card {
          padding: 0;
          overflow: hidden;
        }
        .table-meta-bar {
          display: flex;
          justify-content: space-between;
          align-items: center;
          padding: 12px 18px;
          background-color: var(--bg-surface-subtle);
          border-bottom: 1px solid var(--border-subtle);
          font-size: 0.8rem;
          color: var(--text-secondary);
        }
        .table-responsive {
          overflow-x: auto;
        }
        .queue-table {
          width: 100%;
          border-collapse: collapse;
          text-align: left;
          font-size: 0.85rem;
        }
        .queue-table th {
          padding: 12px 16px;
          font-weight: 600;
          color: var(--text-secondary);
          background-color: var(--bg-surface);
          border-bottom: 1px solid var(--border-subtle);
        }
        .queue-row {
          cursor: pointer;
          transition: background-color 0.15s;
          border-bottom: 1px solid var(--border-subtle);
        }
        .queue-row:hover {
          background-color: #f8fafc;
        }
        .queue-table td {
          padding: 14px 16px;
          vertical-align: middle;
        }
        .doc-name {
          font-weight: 600;
          color: var(--text-primary);
        }
        .doc-id {
          font-size: 0.725rem;
          color: var(--text-muted);
        }
        .doc-date {
          font-size: 0.8rem;
          color: var(--text-muted);
        }
        .patient-pill {
          background-color: var(--bg-surface-subtle);
          border: 1px solid var(--border-subtle);
          padding: 2px 8px;
          border-radius: var(--radius-sm);
          font-size: 0.75rem;
          color: var(--text-secondary);
        }
        .status-dot-badge {
          display: inline-flex;
          align-items: center;
          gap: 5px;
          font-size: 0.775rem;
          font-weight: 600;
          padding: 2px 8px;
          border-radius: var(--radius-full);
        }
        .dot-green { background-color: #dcfce7; color: #166534; }
        .dot-purple { background-color: #f3e8ff; color: #6b21a8; }
        .dot-red { background-color: #fee2e2; color: #991b1b; }
        .dot-amber { background-color: #fef3c7; color: #92400e; }
        .claims-stats {
          display: flex;
          gap: 6px;
          align-items: center;
          flex-wrap: wrap;
        }
        .total-claims {
          font-weight: 600;
          color: var(--text-primary);
        }
        .discrepancy-pill {
          background-color: #fee2e2;
          color: #991b1b;
          font-size: 0.725rem;
          font-weight: 600;
          padding: 1px 6px;
          border-radius: var(--radius-sm);
        }
        .review-req-pill {
          background-color: #fef3c7;
          color: #92400e;
          font-size: 0.725rem;
          font-weight: 600;
          padding: 1px 6px;
          border-radius: var(--radius-sm);
        }
        .btn-sm {
          padding: 5px 10px;
          font-size: 0.8rem;
        }
        .th-action, .td-action {
          text-align: right;
        }
        .font-mono {
          font-family: var(--font-mono);
        }
        .spinner {
          width: 32px;
          height: 32px;
          border: 3px solid var(--border-subtle);
          border-top-color: var(--color-primary);
          border-radius: 50%;
          animation: spin 0.8s linear infinite;
        }
        @keyframes spin {
          to { transform: rotate(360deg); }
        }
      `}</style>
    </div>
  );
};
