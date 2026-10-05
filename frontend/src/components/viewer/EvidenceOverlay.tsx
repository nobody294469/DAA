/**
 * Interactive SVG Evidence Overlay Component
 * 
 * Authority:
 *   - PRODUCTIZATION_SPEC.md §7.3 (Reviewer sees source page with evidence highlighted)
 *   - PROJECT_SPEC.md §9 (Grounding evidence bounding boxes)
 * 
 * Renders an SVG overlay perfectly synchronized to the active PDF page canvas.
 * Evidence bounding boxes are rendered by default to mitigate automation bias.
 */

import React, { useMemo } from 'react';
import type { DocumentClaimItem } from '../../types';
import { transformBoundingBox, type ViewportBox } from '../../utils/coordinates';

export interface EvidenceOverlayProps {
  claims: DocumentClaimItem[];
  currentPage: number;
  viewportWidth: number;
  viewportHeight: number;
  pdfPageWidth?: number;
  pdfPageHeight?: number;
  selectedClaimId?: string | null;
  selectedEvidenceId?: string | null;
  onSelectEvidence?: (claimId: string, evidenceId?: string | null) => void;
  visible?: boolean;
}

interface RenderableEvidence {
  claimId: string;
  evidenceId?: string | null;
  fieldType: string;
  value: string;
  textSnippet?: string | null;
  verificationLabel?: string;
  box: ViewportBox;
  isSelected: boolean;
}

export const EvidenceOverlay: React.FC<EvidenceOverlayProps> = ({
  claims,
  currentPage,
  viewportWidth,
  viewportHeight,
  pdfPageWidth,
  pdfPageHeight,
  selectedClaimId,
  selectedEvidenceId,
  onSelectEvidence,
  visible = true,
}) => {
  // Transform and filter claims for current page
  const pageEvidence = useMemo<RenderableEvidence[]>(() => {
    if (!claims || claims.length === 0 || viewportWidth <= 0 || viewportHeight <= 0) {
      return [];
    }

    const items: RenderableEvidence[] = [];

    for (const claim of claims) {
      if (!claim.grounding) continue;
      if (claim.grounding.page_number !== currentPage) continue;

      const rawBbox = claim.grounding.bounding_box;
      if (!rawBbox) continue;

      const box = transformBoundingBox(
        rawBbox,
        viewportWidth,
        viewportHeight,
        pdfPageWidth,
        pdfPageHeight
      );

      if (!box) continue;

      const isSelected =
        (selectedClaimId && claim.claim_id === selectedClaimId) ||
        (selectedEvidenceId && claim.evidence_id === selectedEvidenceId) ||
        false;

      items.push({
        claimId: claim.claim_id,
        evidenceId: claim.evidence_id,
        fieldType: claim.field_type,
        value: claim.value,
        textSnippet: claim.grounding.text_snippet,
        verificationLabel: claim.verification?.label,
        box,
        isSelected,
      });
    }

    return items;
  }, [
    claims,
    currentPage,
    viewportWidth,
    viewportHeight,
    pdfPageWidth,
    pdfPageHeight,
    selectedClaimId,
    selectedEvidenceId,
  ]);

  if (!visible || viewportWidth <= 0 || viewportHeight <= 0) {
    return null;
  }

  const getColors = (item: RenderableEvidence) => {
    if (item.isSelected) {
      return {
        stroke: '#2563eb', // Prominent blue
        strokeWidth: 3,
        fill: 'rgba(37, 99, 235, 0.35)',
      };
    }

    const label = (item.verificationLabel || '').toUpperCase();
    if (label.includes('DISCREPANCY')) {
      return {
        stroke: '#d97706', // Amber-600
        strokeWidth: 2,
        fill: 'rgba(245, 158, 11, 0.22)',
      };
    }
    if (label.includes('INSUFFICIENT')) {
      return {
        stroke: '#ca8a04', // Yellow-600
        strokeWidth: 2,
        fill: 'rgba(234, 179, 8, 0.22)',
      };
    }
    if (label.includes('SUPPORTED')) {
      return {
        stroke: '#059669', // Emerald-600
        strokeWidth: 2,
        fill: 'rgba(16, 185, 129, 0.22)',
      };
    }

    // Default neutral/pipeline highlight
    return {
      stroke: '#0284c7', // Sky-600
      strokeWidth: 2,
      fill: 'rgba(2, 132, 199, 0.20)',
    };
  };

  return (
    <svg
      data-testid="evidence-overlay-svg"
      role="region"
      aria-label={`Evidence Highlights for Page ${currentPage}`}
      style={{
        position: 'absolute',
        top: 0,
        left: 0,
        width: `${viewportWidth}px`,
        height: `${viewportHeight}px`,
        pointerEvents: 'none',
        zIndex: 10,
      }}
      viewBox={`0 0 ${viewportWidth} ${viewportHeight}`}
    >
      <defs>
        <filter id="evidence-glow" x="-10%" y="-10%" width="120%" height="120%">
          <feDropShadow dx="0" dy="1" stdDeviation="2" floodColor="#2563eb" floodOpacity="0.5" />
        </filter>
      </defs>

      {pageEvidence.map((item, idx) => {
        const { stroke, strokeWidth, fill } = getColors(item);
        const { x, y, width, height } = item.box;

        // Untrusted text sanitized through JSX text interpolation only (NO dangerouslySetInnerHTML)
        const tooltipText = `Claim: ${item.fieldType} = ${item.value}${
          item.textSnippet ? `\nSnippet: "${item.textSnippet}"` : ''
        }`;

        return (
          <g
            key={`evidence-${item.claimId}-${idx}`}
            data-testid={`evidence-highlight-${item.claimId}`}
            style={{ pointerEvents: 'auto', cursor: 'pointer' }}
            onClick={(e) => {
              e.stopPropagation();
              if (onSelectEvidence) {
                onSelectEvidence(item.claimId, item.evidenceId);
              }
            }}
          >
            <rect
              x={x}
              y={y}
              width={width}
              height={height}
              rx={3}
              ry={3}
              fill={fill}
              stroke={stroke}
              strokeWidth={strokeWidth}
              filter={item.isSelected ? 'url(#evidence-glow)' : undefined}
            />
            {/* Native SVG tooltip */}
            <title>{tooltipText}</title>
          </g>
        );
      })}
    </svg>
  );
};
