/**
 * AppHeader Component — Top bar with clinical mode indicator and user context
 * 
 * Authority:
 *   - PRODUCTIZATION_SPEC.md §7.2, §7.3
 *   - PRODUCTIZATION_DECISIONS.md PD-002, PD-009, PD-013
 */

import React, { useEffect, useState } from 'react';
import { useAuth } from '../../context/AuthContext';
import { healthApi } from '../../api';
import type { CanonicalRole } from '../../types';

export const AppHeader: React.FC = () => {
  const { user, activeRole, setActiveRole } = useAuth();
  const [backendLive, setBackendLive] = useState<boolean | null>(null);

  useEffect(() => {
    let isMounted = true;
    const checkLive = async () => {
      try {
        const res = await healthApi.getLive();
        if (isMounted) setBackendLive(res.status === 'live');
      } catch {
        if (isMounted) setBackendLive(false);
      }
    };
    checkLive();
    const interval = setInterval(checkLive, 30000);
    return () => {
      isMounted = false;
      clearInterval(interval);
    };
  }, []);

  const handleRoleChange = (e: React.ChangeEvent<HTMLSelectElement>) => {
    setActiveRole(e.target.value as CanonicalRole);
  };

  const getRoleClass = (role: CanonicalRole) => {
    switch (role) {
      case 'clinical_reviewer':
        return 'role-pill role-reviewer';
      case 'approver':
        return 'role-pill role-approver';
      case 'document_operator':
        return 'role-pill role-operator';
      case 'system_admin':
        return 'role-pill role-admin';
      default:
        return 'role-pill';
    }
  };

  return (
    <header className="app-header">
      <div className="header-left">
        <div className="brand-logo">🏥</div>
        <div className="brand-text">
          <div className="brand-title">Clindoc2FHIR</div>
          <div className="brand-subtitle">Clinical Review Workbench (PD-013)</div>
        </div>
        <div className="mode-badge">
          <span className="mode-dot"></span>
          Mode 0 (100% Human Review)
        </div>
      </div>

      <div className="header-right">
        {/* Backend Connectivity Status */}
        <div
          className={`status-pill ${
            backendLive === true ? 'status-live' : backendLive === false ? 'status-down' : 'status-checking'
          }`}
          title={backendLive === true ? 'FastAPI Backend Live' : 'Backend Unreachable'}
        >
          <span className="status-dot"></span>
          {backendLive === true ? 'Backend Live' : backendLive === false ? 'Backend Offline' : 'Connecting...'}
        </div>

        {/* User Identity and Active Role */}
        {user && (
          <div className="user-profile">
            <span className="user-name">{user.fullName}</span>
            <span className={getRoleClass(activeRole)}>{activeRole.replace('_', ' ')}</span>
            
            {/* Dev role selector */}
            <select
              aria-label="Switch active role for testing"
              className="role-selector"
              value={activeRole}
              onChange={handleRoleChange}
              title="Switch role for RBAC testing"
            >
              <option value="clinical_reviewer">Reviewer</option>
              <option value="approver">Approver</option>
              <option value="document_operator">Operator</option>
              <option value="system_admin">System Admin</option>
              <option value="auditor">Auditor</option>
            </select>
          </div>
        )}
      </div>

      <style>{`
        .app-header {
          display: flex;
          align-items: center;
          justify-content: space-between;
          height: var(--header-height);
          padding: 0 20px;
          background-color: var(--bg-surface);
          border-bottom: 1px solid var(--border-subtle);
          box-shadow: var(--shadow-sm);
        }
        .header-left, .header-right {
          display: flex;
          align-items: center;
          gap: 16px;
        }
        .brand-logo {
          font-size: 1.6rem;
        }
        .brand-title {
          font-size: 1.05rem;
          font-weight: 700;
          color: var(--text-primary);
          line-height: 1.2;
        }
        .brand-subtitle {
          font-size: 0.725rem;
          color: var(--text-muted);
        }
        .mode-badge {
          display: inline-flex;
          align-items: center;
          gap: 6px;
          background-color: #f0f9ff;
          border: 1px solid #bae6fd;
          color: #0369a1;
          font-size: 0.75rem;
          font-weight: 600;
          padding: 3px 10px;
          border-radius: var(--radius-full);
        }
        .mode-dot {
          width: 6px;
          height: 6px;
          border-radius: 50%;
          background-color: #0284c7;
        }
        .status-pill {
          display: inline-flex;
          align-items: center;
          gap: 6px;
          font-size: 0.75rem;
          font-weight: 500;
          padding: 3px 9px;
          border-radius: var(--radius-full);
          border: 1px solid transparent;
        }
        .status-dot {
          width: 6px;
          height: 6px;
          border-radius: 50%;
        }
        .status-live {
          background-color: #f0fdf4;
          border-color: #bbf7d0;
          color: #166534;
        }
        .status-live .status-dot { background-color: #22c55e; }
        .status-down {
          background-color: #fef2f2;
          border-color: #fecaca;
          color: #991b1b;
        }
        .status-down .status-dot { background-color: #ef4444; }
        .status-checking {
          background-color: #f8fafc;
          border-color: #e2e8f0;
          color: #64748b;
        }
        .user-profile {
          display: flex;
          align-items: center;
          gap: 8px;
        }
        .user-name {
          font-size: 0.85rem;
          font-weight: 500;
          color: var(--text-secondary);
        }
        .role-pill {
          font-size: 0.7rem;
          font-weight: 600;
          text-transform: capitalize;
          padding: 2px 8px;
          border-radius: var(--radius-sm);
        }
        .role-reviewer { background-color: var(--role-reviewer-bg); color: var(--role-reviewer-text); }
        .role-approver { background-color: var(--role-approver-bg); color: var(--role-approver-text); }
        .role-operator { background-color: var(--role-operator-bg); color: var(--role-operator-text); }
        .role-admin { background-color: var(--role-admin-bg); color: var(--role-admin-text); }
        .role-selector {
          font-size: 0.75rem;
          padding: 2px 4px;
          border: 1px solid var(--border-subtle);
          border-radius: var(--radius-sm);
          background-color: var(--bg-surface-subtle);
          color: var(--text-secondary);
        }
      `}</style>
    </header>
  );
};
