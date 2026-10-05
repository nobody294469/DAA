/**
 * High-DPI PDF.js Document Viewer with Pan, Zoom & Synchronized Evidence Overlays
 * 
 * Authority:
 *   - PRODUCTIZATION_SPEC.md §7.3 (Reviewer sees source page with evidence highlighted)
 *   - PRODUCTIZATION_DECISIONS.md PD-013 (High-DPI PDF.js rendering in React SPA)
 *   - PROJECT_SPEC.md §13.1 (Mitigating automation bias via persistent evidence visibility)
 */

import React, { useState, useEffect, useRef, useCallback } from 'react';
import * as pdfjsLib from 'pdfjs-dist';
import { reviewApi, ApiClientError } from '../../api';
import type { DocumentClaimItem } from '../../types';
import { EvidenceOverlay } from './EvidenceOverlay';

// Configure PDF.js worker
if (typeof window !== 'undefined' && !pdfjsLib.GlobalWorkerOptions.workerSrc) {
  try {
    pdfjsLib.GlobalWorkerOptions.workerSrc = new URL(
      'pdfjs-dist/build/pdf.worker.min.mjs',
      import.meta.url
    ).toString();
  } catch {
    // Fallback if URL resolution is not supported
  }
}

export interface PdfViewerProps {
  documentId: string;
  claims?: DocumentClaimItem[];
  selectedClaimId?: string | null;
  selectedEvidenceId?: string | null;
  onSelectClaim?: (claimId: string, evidenceId?: string | null) => void;
  className?: string;
}

export const PdfViewer: React.FC<PdfViewerProps> = ({
  documentId,
  claims = [],
  selectedClaimId,
  selectedEvidenceId,
  onSelectClaim,
  className,
}) => {
  // Document state
  const [pdfDoc, setPdfDoc] = useState<any | null>(null);
  const [numPages, setNumPages] = useState<number>(0);
  const [currentPage, setCurrentPage] = useState<number>(1);
  const [inputPage, setInputPage] = useState<string>('1');

  // Zoom & Viewport state
  const [zoom, setZoom] = useState<number>(1.0);
  const [viewportDims, setViewportDims] = useState<{ width: number; height: number }>({ width: 0, height: 0 });
  const [naturalDims, setNaturalDims] = useState<{ width: number; height: number }>({ width: 0, height: 0 });
  const [showHighlights, setShowHighlights] = useState<boolean>(true);

  // Status & error states
  const [loading, setLoading] = useState<boolean>(true);
  const [rendering, setRendering] = useState<boolean>(false);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);
  const [errorStatus, setErrorStatus] = useState<number | null>(null);
  const [correlationId, setCorrelationId] = useState<string | null>(null);

  // DOM Refs
  const containerRef = useRef<HTMLDivElement>(null);
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const renderTaskRef = useRef<any>(null);

  // Pan / Drag State
  const [isPanning, setIsPanning] = useState<boolean>(false);
  const panStartRef = useRef<{ startX: number; startY: number; scrollLeft: number; scrollTop: number }>({
    startX: 0,
    startY: 0,
    scrollLeft: 0,
    scrollTop: 0,
  });

  // If a claim is selected from another component, automatically navigate to its evidence page
  useEffect(() => {
    if (!selectedClaimId || !claims || claims.length === 0) return;
    const targetClaim = claims.find((c) => c.claim_id === selectedClaimId);
    if (targetClaim && targetClaim.grounding && targetClaim.grounding.page_number) {
      const targetPage = targetClaim.grounding.page_number;
      if (targetPage >= 1 && targetPage <= numPages && targetPage !== currentPage) {
        setCurrentPage(targetPage);
        setInputPage(String(targetPage));
      }
    }
  }, [selectedClaimId, claims, numPages, currentPage]);

  // Load PDF Document bytes from backend
  const loadDocument = useCallback(async () => {
    if (!documentId) return;

    setLoading(true);
    setErrorMessage(null);
    setErrorStatus(null);
    setCorrelationId(null);
    setPdfDoc(null);
    setNumPages(0);
    setCurrentPage(1);
    setInputPage('1');

    try {
      const blob = await reviewApi.getDocumentFile(documentId);
      const arrayBuffer = await blob.arrayBuffer();

      const loadingTask = pdfjsLib.getDocument({
        data: arrayBuffer,
        cMapUrl: 'https://cdn.jsdelivr.net/npm/pdfjs-dist@6.4.299/cmaps/',
        cMapPacked: true,
      });

      const loadedPdf = await loadingTask.promise;
      setPdfDoc(loadedPdf);
      setNumPages(loadedPdf.numPages);
      setLoading(false);
    } catch (err: any) {
      setLoading(false);
      if (err instanceof ApiClientError) {
        setErrorStatus(err.status);
        setCorrelationId(err.correlationId || null);
        if (err.status === 404) {
          setErrorMessage('The requested document was not found or has been purged from encrypted storage.');
        } else if (err.status === 401 || err.status === 403) {
          setErrorMessage('Access denied. You do not have permission to view this document.');
        } else {
          setErrorMessage(`API Error: ${err.message}`);
        }
      } else {
        setErrorMessage(err.message || 'Failed to render PDF document.');
      }
    }
  }, [documentId]);

  useEffect(() => {
    loadDocument();

    return () => {
      if (renderTaskRef.current) {
        renderTaskRef.current.cancel();
      }
    };
  }, [loadDocument]);

  // Render Page
  const renderCurrentPage = useCallback(async () => {
    if (!pdfDoc || currentPage < 1 || currentPage > numPages || !canvasRef.current) {
      return;
    }

    if (renderTaskRef.current) {
      try {
        renderTaskRef.current.cancel();
      } catch {
        // ignore cancellation
      }
    }

    setRendering(true);

    try {
      const page = await pdfDoc.getPage(currentPage);
      const canvas = canvasRef.current;
      if (!canvas) return;

      const unscaledViewport = page.getViewport({ scale: 1.0 });
      setNaturalDims({
        width: Math.round(unscaledViewport.width),
        height: Math.round(unscaledViewport.height),
      });

      // Calculate High-DPI scale
      const dpr = typeof window !== 'undefined' ? window.devicePixelRatio || 1 : 1;
      const viewport = page.getViewport({ scale: zoom });

      const displayWidth = Math.floor(viewport.width);
      const displayHeight = Math.floor(viewport.height);

      canvas.width = Math.floor(displayWidth * dpr);
      canvas.height = Math.floor(displayHeight * dpr);
      canvas.style.width = `${displayWidth}px`;
      canvas.style.height = `${displayHeight}px`;

      setViewportDims({ width: displayWidth, height: displayHeight });

      const ctx = canvas.getContext('2d');
      if (ctx) {
        ctx.setTransform(1, 0, 0, 1, 0, 0); // Reset transform
        ctx.scale(dpr, dpr);

        const renderContext = {
          canvasContext: ctx,
          viewport: viewport,
        };

        const renderTask = page.render(renderContext);
        renderTaskRef.current = renderTask;
        await renderTask.promise;
      }
      setRendering(false);
    } catch (err: any) {
      if (err?.name !== 'RenderingCancelledException') {
        console.error('PDF rendering error:', err);
      }
      setRendering(false);
    }
  }, [pdfDoc, currentPage, numPages, zoom]);

  useEffect(() => {
    renderCurrentPage();
  }, [renderCurrentPage]);

  // Navigation handlers
  const handlePrevPage = () => {
    if (currentPage > 1) {
      const nextP = currentPage - 1;
      setCurrentPage(nextP);
      setInputPage(String(nextP));
    }
  };

  const handleNextPage = () => {
    if (currentPage < numPages) {
      const nextP = currentPage + 1;
      setCurrentPage(nextP);
      setInputPage(String(nextP));
    }
  };

  const handlePageInputChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    setInputPage(e.target.value);
  };

  const handlePageInputSubmit = (e: React.KeyboardEvent<HTMLInputElement>) => {
    if (e.key === 'Enter') {
      commitPageInput();
    }
  };

  const commitPageInput = () => {
    const parsed = parseInt(inputPage, 10);
    if (!isNaN(parsed) && parsed >= 1 && parsed <= numPages) {
      setCurrentPage(parsed);
      setInputPage(String(parsed));
    } else {
      setInputPage(String(currentPage));
    }
  };

  // Zoom handlers
  const handleZoomIn = () => {
    setZoom((prev) => Math.min(3.0, Math.round((prev + 0.25) * 100) / 100));
  };

  const handleZoomOut = () => {
    setZoom((prev) => Math.max(0.5, Math.round((prev - 0.25) * 100) / 100));
  };

  const handleResetZoom = () => {
    setZoom(1.0);
  };

  const handleFitWidth = () => {
    if (containerRef.current && naturalDims.width > 0) {
      const availableWidth = containerRef.current.clientWidth - 48; // padding margin
      if (availableWidth > 100) {
        const calculatedScale = Math.round((availableWidth / naturalDims.width) * 100) / 100;
        setZoom(Math.max(0.5, Math.min(3.0, calculatedScale)));
      }
    }
  };

  // Drag-to-pan handlers
  const handleMouseDown = (e: React.MouseEvent<HTMLDivElement>) => {
    if (!containerRef.current || e.button !== 0) return;
    // Don't trigger panning if clicking interactive evidence highlights or buttons
    if ((e.target as HTMLElement).tagName.toLowerCase() === 'rect' || (e.target as HTMLElement).closest('button')) {
      return;
    }

    setIsPanning(true);
    panStartRef.current = {
      startX: e.clientX,
      startY: e.clientY,
      scrollLeft: containerRef.current.scrollLeft,
      scrollTop: containerRef.current.scrollTop,
    };
  };

  const handleMouseMove = (e: React.MouseEvent<HTMLDivElement>) => {
    if (!isPanning || !containerRef.current) return;
    const dx = e.clientX - panStartRef.current.startX;
    const dy = e.clientY - panStartRef.current.startY;
    containerRef.current.scrollLeft = panStartRef.current.scrollLeft - dx;
    containerRef.current.scrollTop = panStartRef.current.scrollTop - dy;
  };

  const handleMouseUp = () => {
    setIsPanning(false);
  };

  // Loading State
  if (loading) {
    return (
      <div
        data-testid="pdf-viewer-loading"
        className={className}
        style={{
          display: 'flex',
          flexDirection: 'column',
          alignItems: 'center',
          justifyContent: 'center',
          minHeight: '450px',
          background: 'var(--color-bg)',
          border: '1px solid var(--color-border)',
          borderRadius: 'var(--radius-md)',
          padding: '24px',
        }}
      >
        <div
          style={{
            width: '36px',
            height: '36px',
            border: '3px solid var(--color-border)',
            borderTopColor: 'var(--color-primary)',
            borderRadius: '50%',
            animation: 'spin 1s linear infinite',
            marginBottom: '16px',
          }}
        />
        <div style={{ color: 'var(--color-text)', fontWeight: 600 }}>Loading document...</div>
        <div style={{ color: 'var(--color-text-muted)', fontSize: '13px', marginTop: '4px' }}>
          Decrypting and preparing high-DPI PDF view
        </div>
      </div>
    );
  }

  // Error State
  if (errorMessage) {
    return (
      <div
        data-testid="pdf-viewer-error"
        className={className}
        style={{
          display: 'flex',
          flexDirection: 'column',
          alignItems: 'center',
          justifyContent: 'center',
          minHeight: '400px',
          background: 'var(--color-bg)',
          border: '1px solid #fca5a5',
          borderRadius: 'var(--radius-md)',
          padding: '32px',
          textAlign: 'center',
        }}
      >
        <div
          style={{
            width: '48px',
            height: '48px',
            borderRadius: '50%',
            background: '#fee2e2',
            color: '#dc2626',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            fontSize: '24px',
            marginBottom: '16px',
          }}
        >
          ✕
        </div>
        <h3 style={{ margin: '0 0 8px', color: '#991b1b', fontSize: '18px' }}>
          {errorStatus === 404
            ? 'Document Not Found'
            : errorStatus === 403 || errorStatus === 401
            ? 'Access Denied'
            : 'Failed to Load Document'}
        </h3>
        <p style={{ margin: '0 0 16px', color: 'var(--color-text-muted)', maxWidth: '440px', fontSize: '14px' }}>
          {errorMessage}
        </p>
        {correlationId && (
          <div style={{ fontSize: '11px', color: 'var(--color-text-muted)', marginBottom: '16px', fontFamily: 'monospace' }}>
            Correlation ID: {correlationId}
          </div>
        )}
        <button
          onClick={loadDocument}
          style={{
            padding: '8px 16px',
            background: 'var(--color-primary)',
            color: '#fff',
            border: 'none',
            borderRadius: 'var(--radius-sm)',
            cursor: 'pointer',
            fontWeight: 600,
          }}
        >
          Retry
        </button>
      </div>
    );
  }

  return (
    <div
      data-testid="pdf-viewer-container"
      className={className}
      style={{
        display: 'flex',
        flexDirection: 'column',
        height: '100%',
        minHeight: '600px',
        background: 'var(--color-surface)',
        border: '1px solid var(--color-border)',
        borderRadius: 'var(--radius-md)',
        overflow: 'hidden',
      }}
    >
      {/* ─── TOOLBAR ────────────────────────────────────────────── */}
      <div
        data-testid="pdf-toolbar"
        style={{
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
          flexWrap: 'wrap',
          gap: '12px',
          padding: '8px 16px',
          background: 'var(--color-bg)',
          borderBottom: '1px solid var(--color-border)',
          fontSize: '13px',
        }}
      >
        {/* Page Navigation */}
        <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
          <button
            data-testid="pdf-prev-page"
            disabled={currentPage <= 1 || rendering}
            onClick={handlePrevPage}
            aria-label="Previous Page"
            style={{
              padding: '4px 10px',
              background: 'var(--color-surface)',
              border: '1px solid var(--color-border)',
              borderRadius: 'var(--radius-sm)',
              cursor: currentPage <= 1 ? 'not-allowed' : 'pointer',
              opacity: currentPage <= 1 ? 0.5 : 1,
            }}
          >
            ◀ Prev
          </button>
          <div style={{ display: 'flex', alignItems: 'center', gap: '4px' }}>
            <span>Page</span>
            <input
              data-testid="pdf-page-input"
              type="text"
              value={inputPage}
              onChange={handlePageInputChange}
              onKeyDown={handlePageInputSubmit}
              onBlur={commitPageInput}
              style={{
                width: '42px',
                textAlign: 'center',
                padding: '3px 4px',
                border: '1px solid var(--color-border)',
                borderRadius: 'var(--radius-sm)',
                background: 'var(--color-surface)',
                color: 'var(--color-text)',
                fontWeight: 600,
              }}
              aria-label="Current Page Number"
            />
            <span>of {numPages}</span>
          </div>
          <button
            data-testid="pdf-next-page"
            disabled={currentPage >= numPages || rendering}
            onClick={handleNextPage}
            aria-label="Next Page"
            style={{
              padding: '4px 10px',
              background: 'var(--color-surface)',
              border: '1px solid var(--color-border)',
              borderRadius: 'var(--radius-sm)',
              cursor: currentPage >= numPages ? 'not-allowed' : 'pointer',
              opacity: currentPage >= numPages ? 0.5 : 1,
            }}
          >
            Next ▶
          </button>
        </div>

        {/* Zoom Controls */}
        <div style={{ display: 'flex', alignItems: 'center', gap: '6px' }}>
          <button
            data-testid="pdf-zoom-out"
            disabled={zoom <= 0.5}
            onClick={handleZoomOut}
            aria-label="Zoom Out"
            style={{
              padding: '4px 8px',
              background: 'var(--color-surface)',
              border: '1px solid var(--color-border)',
              borderRadius: 'var(--radius-sm)',
              cursor: zoom <= 0.5 ? 'not-allowed' : 'pointer',
              opacity: zoom <= 0.5 ? 0.5 : 1,
            }}
          >
            −
          </button>
          <button
            data-testid="pdf-zoom-reset"
            onClick={handleResetZoom}
            aria-label="Reset Zoom"
            style={{
              padding: '4px 10px',
              background: 'var(--color-surface)',
              border: '1px solid var(--color-border)',
              borderRadius: 'var(--radius-sm)',
              cursor: 'pointer',
              fontWeight: 600,
            }}
          >
            {Math.round(zoom * 100)}%
          </button>
          <button
            data-testid="pdf-zoom-in"
            disabled={zoom >= 3.0}
            onClick={handleZoomIn}
            aria-label="Zoom In"
            style={{
              padding: '4px 8px',
              background: 'var(--color-surface)',
              border: '1px solid var(--color-border)',
              borderRadius: 'var(--radius-sm)',
              cursor: zoom >= 3.0 ? 'not-allowed' : 'pointer',
              opacity: zoom >= 3.0 ? 0.5 : 1,
            }}
          >
            +
          </button>
          <button
            data-testid="pdf-fit-width"
            onClick={handleFitWidth}
            style={{
              padding: '4px 10px',
              background: 'var(--color-surface)',
              border: '1px solid var(--color-border)',
              borderRadius: 'var(--radius-sm)',
              cursor: 'pointer',
            }}
          >
            Fit Width
          </button>
        </div>

        {/* Evidence Highlights Visibility Toggle */}
        <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
          <label style={{ display: 'flex', alignItems: 'center', gap: '6px', cursor: 'pointer', userSelect: 'none' }}>
            <input
              data-testid="pdf-highlights-toggle"
              type="checkbox"
              checked={showHighlights}
              onChange={(e) => setShowHighlights(e.target.checked)}
            />
            <span style={{ fontWeight: 500 }}>Highlights</span>
          </label>
        </div>
      </div>

      {/* ─── SCROLLABLE / PANNABLE VIEWPORT ─────────────────────── */}
      <div
        ref={containerRef}
        data-testid="pdf-viewport-scroll"
        onMouseDown={handleMouseDown}
        onMouseMove={handleMouseMove}
        onMouseUp={handleMouseUp}
        onMouseLeave={handleMouseUp}
        style={{
          flex: 1,
          overflow: 'auto',
          display: 'flex',
          justifyContent: 'center',
          alignItems: 'flex-start',
          padding: '24px',
          background: '#525659', // Classic neutral PDF backdrop
          cursor: isPanning ? 'grabbing' : 'grab',
          userSelect: 'none',
        }}
      >
        <div
          data-testid="pdf-canvas-wrapper"
          style={{
            position: 'relative',
            width: viewportDims.width > 0 ? `${viewportDims.width}px` : 'auto',
            height: viewportDims.height > 0 ? `${viewportDims.height}px` : 'auto',
            boxShadow: '0 4px 16px rgba(0, 0, 0, 0.35)',
            background: '#ffffff',
            borderRadius: '2px',
          }}
        >
          <canvas
            ref={canvasRef}
            data-testid="pdf-render-canvas"
            style={{
              display: 'block',
              width: `${viewportDims.width}px`,
              height: `${viewportDims.height}px`,
            }}
          />

          {/* Synchronized SVG Evidence Overlay */}
          <EvidenceOverlay
            claims={claims}
            currentPage={currentPage}
            viewportWidth={viewportDims.width}
            viewportHeight={viewportDims.height}
            pdfPageWidth={naturalDims.width}
            pdfPageHeight={naturalDims.height}
            selectedClaimId={selectedClaimId}
            selectedEvidenceId={selectedEvidenceId}
            onSelectEvidence={onSelectClaim}
            visible={showHighlights}
          />

          {rendering && (
            <div
              style={{
                position: 'absolute',
                top: '12px',
                right: '12px',
                background: 'rgba(0, 0, 0, 0.75)',
                color: '#fff',
                fontSize: '11px',
                padding: '4px 8px',
                borderRadius: '4px',
                pointerEvents: 'none',
              }}
            >
              Rendering...
            </div>
          )}
        </div>
      </div>
    </div>
  );
};
