/**
 * SystemStatusView Component — Backend Connection & RBAC Diagnostics
 * 
 * Authority:
 *   - PRODUCTIZATION_SPEC.md §9.3
 *   - PRODUCTIZATION_DECISIONS.md PD-013
 */

import React, { useState } from 'react';
import { useAuth } from '../../context/AuthContext';
import { healthApi, ApiClientError } from '../../api';

export const SystemStatusView: React.FC = () => {
  const { user, token, activeRole, setAuth } = useAuth();
  const [liveStatus, setLiveStatus] = useState<string | null>(null);
  const [readyStatus, setReadyStatus] = useState<string | null>(null);
  const [lastCheckTime, setLastCheckTime] = useState<string | null>(null);
  const [errorMsg, setErrorMsg] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [manualToken, setManualToken] = useState(token || '');

  const runHealthProbes = async () => {
    setLoading(true);
    setErrorMsg(null);
    try {
      const liveRes = await healthApi.getLive();
      setLiveStatus(JSON.stringify(liveRes));

      try {
        const readyRes = await healthApi.getReady();
        setReadyStatus(JSON.stringify(readyRes));
      } catch (err: any) {
        if (err instanceof ApiClientError) {
          setReadyStatus(`HTTP ${err.status}: ${err.detail}`);
        } else {
          setReadyStatus(err.message);
        }
      }

      setLastCheckTime(new Date().toLocaleTimeString());
    } catch (err: any) {
      setErrorMsg(err.message || 'Probe failed');
    } finally {
      setLoading(false);
    }
  };

  const handleUpdateToken = () => {
    if (user) {
      setAuth(manualToken.trim() || null, user);
    }
  };

  return (
    <div className="system-container">
      <div className="view-header">
        <h2>Backend Connectivity & Security Diagnostics</h2>
        <p className="view-subtitle">
          Verify connection to FastAPI REST backend (<code>/api/v1</code>) and RBAC session parameters.
        </p>
      </div>

      <div className="status-grid">
        <div className="card">
          <h3>Health Endpoints Probe</h3>
          <p className="card-desc">Tests unauthenticated <code>/health/live</code> and authenticated <code>/health/ready</code>.</p>
          
          <div className="probe-results">
            <div className="probe-row">
              <span className="probe-label">GET /api/v1/health/live:</span>
              <span className="probe-value">{liveStatus || 'Not checked'}</span>
            </div>
            <div className="probe-row">
              <span className="probe-label">GET /api/v1/health/ready:</span>
              <span className="probe-value">{readyStatus || 'Not checked'}</span>
            </div>
            {lastCheckTime && (
              <div className="probe-time">Last probe: {lastCheckTime}</div>
            )}
          </div>

          {errorMsg && <div className="error-banner">{errorMsg}</div>}

          <button className="btn btn-primary" onClick={runHealthProbes} disabled={loading}>
            {loading ? 'Probing...' : 'Run Probe Now'}
          </button>
        </div>

        <div className="card">
          <h3>Active Session & Token Configuration</h3>
          <p className="card-desc">Authorization headers attached to all outbound REST requests.</p>

          <div className="session-details">
            <div className="detail-row">
              <span className="detail-label">Active Role:</span>
              <span className="badge badge-supported">{activeRole}</span>
            </div>
            <div className="detail-row">
              <span className="detail-label">User Subject:</span>
              <code>{user?.userId}</code>
            </div>
            <div className="detail-row">
              <span className="detail-label">Username:</span>
              <span>{user?.username} ({user?.email})</span>
            </div>
          </div>

          <div className="token-editor">
            <label htmlFor="auth-token-input">Bearer Token (JWT / Test token):</label>
            <input
              id="auth-token-input"
              type="text"
              value={manualToken}
              onChange={(e) => setManualToken(e.target.value)}
              placeholder="Paste JWT bearer token..."
            />
            <button className="btn btn-secondary" onClick={handleUpdateToken}>
              Update Token
            </button>
          </div>
        </div>
      </div>

      <style>{`
        .system-container {
          max-width: 1100px;
          margin: 0 auto;
        }
        .view-header {
          margin-bottom: 24px;
        }
        .view-header h2 {
          font-size: 1.35rem;
          font-weight: 700;
          color: var(--text-primary);
        }
        .view-subtitle {
          font-size: 0.875rem;
          color: var(--text-muted);
          margin-top: 4px;
        }
        .status-grid {
          display: grid;
          grid-template-columns: 1fr 1fr;
          gap: 20px;
        }
        .card h3 {
          font-size: 1.05rem;
          font-weight: 600;
          margin-bottom: 6px;
        }
        .card-desc {
          font-size: 0.8rem;
          color: var(--text-muted);
          margin-bottom: 16px;
        }
        .probe-results, .session-details {
          background-color: var(--bg-surface-subtle);
          border: 1px solid var(--border-subtle);
          border-radius: var(--radius-md);
          padding: 14px;
          margin-bottom: 16px;
        }
        .probe-row, .detail-row {
          display: flex;
          justify-content: space-between;
          align-items: center;
          padding: 6px 0;
          font-size: 0.825rem;
          border-bottom: 1px solid var(--border-subtle);
        }
        .probe-row:last-child, .detail-row:last-child {
          border-bottom: none;
        }
        .probe-label, .detail-label {
          font-weight: 600;
          color: var(--text-secondary);
        }
        .probe-value {
          font-family: var(--font-mono);
          color: var(--color-primary);
        }
        .probe-time {
          font-size: 0.725rem;
          color: var(--text-muted);
          margin-top: 8px;
          text-align: right;
        }
        .error-banner {
          background-color: #fee2e2;
          color: #991b1b;
          padding: 8px 12px;
          border-radius: var(--radius-sm);
          font-size: 0.8rem;
          margin-bottom: 12px;
        }
        .token-editor {
          display: flex;
          flex-direction: column;
          gap: 8px;
          margin-top: 14px;
        }
        .token-editor label {
          font-size: 0.775rem;
          font-weight: 600;
          color: var(--text-secondary);
        }
        .token-editor input {
          font-family: var(--font-mono);
          font-size: 0.8rem;
          padding: 8px 10px;
          border: 1px solid var(--border-strong);
          border-radius: var(--radius-sm);
          background-color: var(--bg-surface);
        }
      `}</style>
    </div>
  );
};
