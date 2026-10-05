/**
 * Main Application Shell — Clindoc2FHIR Review Workbench
 * 
 * Authority:
 *   - PRODUCTIZATION_SPEC.md §7.3, §9.3
 *   - PRODUCTIZATION_DECISIONS.md PD-013
 */

import React, { useState } from 'react';
import { AuthProvider } from './context/AuthContext';
import { AppLayout, type NavTab } from './components/layout/AppLayout';
import { QueueView } from './components/views/QueueView';
import { WorkbenchView } from './components/views/WorkbenchView';
import { SystemStatusView } from './components/views/SystemStatusView';
import type { ReviewQueueItem } from './types';

export const AppContent: React.FC = () => {
  const [activeTab, setActiveTab] = useState<NavTab>('queue');
  const [selectedDocument, setSelectedDocument] = useState<ReviewQueueItem | null>(null);

  const handleSelectDocument = (doc: ReviewQueueItem) => {
    setSelectedDocument(doc);
    setActiveTab('workbench');
  };

  const handleBackToQueue = () => {
    setActiveTab('queue');
  };

  return (
    <AppLayout activeTab={activeTab} onTabChange={setActiveTab}>
      {activeTab === 'queue' && (
        <QueueView onSelectDocument={handleSelectDocument} />
      )}
      {activeTab === 'workbench' && (
        <WorkbenchView
          document={selectedDocument}
          onBackToQueue={handleBackToQueue}
        />
      )}
      {activeTab === 'system' && <SystemStatusView />}
    </AppLayout>
  );
};

export const App: React.FC = () => {
  return (
    <AuthProvider>
      <AppContent />
    </AuthProvider>
  );
};

export default App;
