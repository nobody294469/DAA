/**
 * Coordinate Transformation Utilities for PDF Evidence Overlays
 * 
 * Authority:
 *   - PRODUCTIZATION_SPEC.md §7.3
 *   - PROJECT_SPEC.md §9 (schemas/evidence.json bbox contract)
 * 
 * Supports:
 *   - Normalized bounding boxes [0.0..1.0] (e.g. from neural/layout models)
 *   - Point-based bounding boxes [0..pdfWidth, 0..pdfHeight] (standard 72 DPI PDF points)
 *   - Both object format ({ x0, y0, x1, y1 }) and array format ([x0, y0, x1, y1])
 */

import type { BoundingBox, BoundingBoxCoords } from '../types';

export interface ViewportBox {
  x: number;
  y: number;
  width: number;
  height: number;
}

/**
 * Normalizes raw bounding box inputs into a standardized BoundingBoxCoords object.
 */
export function extractBoundingBoxCoords(raw: BoundingBox | null | undefined): BoundingBoxCoords | null {
  if (!raw) return null;

  if (Array.isArray(raw)) {
    if (raw.length < 4) return null;
    const [x0, y0, x1, y1] = raw;
    if (typeof x0 !== 'number' || typeof y0 !== 'number' || typeof x1 !== 'number' || typeof y1 !== 'number') {
      return null;
    }
    return {
      x0: Math.min(x0, x1),
      y0: Math.min(y0, y1),
      x1: Math.max(x0, x1),
      y1: Math.max(y0, y1),
    };
  }

  if (typeof raw === 'object') {
    const { x0, y0, x1, y1 } = raw;
    if (typeof x0 !== 'number' || typeof y0 !== 'number' || typeof x1 !== 'number' || typeof y1 !== 'number') {
      return null;
    }
    return {
      x0: Math.min(x0, x1),
      y0: Math.min(y0, y1),
      x1: Math.max(x0, x1),
      y1: Math.max(y0, y1),
    };
  }

  return null;
}

/**
 * Transforms document/evidence coordinates into viewport pixel coordinates.
 * 
 * @param rawBbox Raw bounding box from evidence
 * @param viewportWidth Rendered width of the PDF page in CSS pixels
 * @param viewportHeight Rendered height of the PDF page in CSS pixels
 * @param pdfPageWidth Unscaled original PDF page width (in points)
 * @param pdfPageHeight Unscaled original PDF page height (in points)
 */
export function transformBoundingBox(
  rawBbox: BoundingBox | null | undefined,
  viewportWidth: number,
  viewportHeight: number,
  pdfPageWidth?: number,
  pdfPageHeight?: number
): ViewportBox | null {
  const coords = extractBoundingBoxCoords(rawBbox);
  if (!coords) return null;

  if (viewportWidth <= 0 || viewportHeight <= 0) return null;

  const { x0, y0, x1, y1 } = coords;

  // Determine whether coords are normalized (0.0 to 1.0)
  const isNormalized = x1 <= 1.0 && y1 <= 1.0 && x0 >= 0 && y0 >= 0;

  let x: number;
  let y: number;
  let width: number;
  let height: number;

  if (isNormalized) {
    x = x0 * viewportWidth;
    y = y0 * viewportHeight;
    width = (x1 - x0) * viewportWidth;
    height = (y1 - y0) * viewportHeight;
  } else if (pdfPageWidth && pdfPageHeight && pdfPageWidth > 0 && pdfPageHeight > 0) {
    const scaleX = viewportWidth / pdfPageWidth;
    const scaleY = viewportHeight / pdfPageHeight;
    x = x0 * scaleX;
    y = y0 * scaleY;
    width = (x1 - x0) * scaleX;
    height = (y1 - y0) * scaleY;
  } else {
    // Fallback: assume coords were in the same scale as viewport
    x = x0;
    y = y0;
    width = x1 - x0;
    height = y1 - y0;
  }

  // Constrain coordinates within viewport bounds
  x = Math.max(0, Math.min(x, viewportWidth));
  y = Math.max(0, Math.min(y, viewportHeight));
  width = Math.max(2, Math.min(width, viewportWidth - x));
  height = Math.max(2, Math.min(height, viewportHeight - y));

  return {
    x: Math.round(x * 100) / 100,
    y: Math.round(y * 100) / 100,
    width: Math.round(width * 100) / 100,
    height: Math.round(height * 100) / 100,
  };
}
