/**
 * AppLayout Component — Shell layout for navigation and workbench views
 * 
 * Authority:
 *   - PRODUCTIZATION_DECISIONS.md PD-013
 */

import React from 'react';
import { AppHeader } from './AppHeader';

export type NavTab = 'queue' | 'workbench' | 'system';

interface AppLayoutProps {
  activeTab: NavTab;
  onTabChange: (tab: NavTab) => void;
  children: React.ReactNode;
}

export const AppLayout: React.FC<AppLayoutProps> = ({
  activeTab,
  onTabChange,
  children,
}) => {
  return (
    <div className="app-shell">
      <AppHeader />
      
      {/* Sub-navigation bar */}
      <nav className="app-nav">
        <div className="nav-container">
          <button
            className={`nav-tab ${activeTab === 'queue' ? 'nav-tab-active' : ''}`}
            onClick={() => onTabChange('queue')}
          >
            📋 Review Queue
          </button>
          <button
            className={`nav-tab ${activeTab === 'workbench' ? 'nav-tab-active' : ''}`}
            onClick={() => onTabChange('workbench')}
          >
            🔬 Review Workbench
          </button>
          <button
            className={`nav-tab ${activeTab === 'system' ? 'nav-tab-active' : ''}`}
            onClick={() => onTabChange('system')}
          >
            ⚙️ System Status
          </button>
        </div>
      </nav>

      <main className="app-main">
        {children}
      </main>

      <style>{`
        .app-shell {
          display: flex;
          flex-direction: column;
          height: 100vh;
          overflow: hidden;
        }
        .app-nav {
          background-color: var(--bg-surface);
          border-bottom: 1px solid var(--border-subtle);
          padding: 0 20px;
        }
        .nav-container {
          display: flex;
          gap: 8px;
        }
        .nav-tab {
          padding: 10px 16px;
          font-size: 0.85rem;
          font-weight: 500;
          color: var(--text-muted);
          border-bottom: 2px solid transparent;
          border-radius: var(--radius-sm) var(--radius-sm) 0 0;
        }
        .nav-tab:hover {
          color: var(--text-primary);
          background-color: var(--bg-surface-subtle);
        }
        .nav-tab-active {
          color: var(--color-primary);
          font-weight: 600;
          border-bottom-color: var(--color-primary);
        }
        .app-main {
          flex: 1;
          overflow: auto;
          background-color: var(--bg-app);
          padding: 20px;
        }
      `}</style>
    </div>
  );
};
