import { describe, it, expect } from 'vitest';
import {
  extractBoundingBoxCoords,
  transformBoundingBox,
} from '../coordinates';

describe('Coordinate Transformation Utilities', () => {
  it('extracts bounding box from 4-element array', () => {
    const extracted = extractBoundingBoxCoords([10, 20, 110, 220]);
    expect(extracted).toEqual({
      x0: 10,
      y0: 20,
      x1: 110,
      y1: 220,
    });
  });

  it('extracts bounding box from object format', () => {
    const extracted = extractBoundingBoxCoords({ x0: 0.1, y0: 0.2, x1: 0.5, y1: 0.6 });
    expect(extracted).toEqual({
      x0: 0.1,
      y0: 0.2,
      x1: 0.5,
      y1: 0.6,
    });
  });

  it('normalizes inverted min/max coordinates safely', () => {
    const extracted = extractBoundingBoxCoords([200, 300, 50, 80]);
    expect(extracted).toEqual({
      x0: 50,
      y0: 80,
      x1: 200,
      y1: 300,
    });
  });

  it('returns null for invalid or null inputs', () => {
    expect(extractBoundingBoxCoords(null)).toBeNull();
    expect(extractBoundingBoxCoords(undefined)).toBeNull();
    expect(extractBoundingBoxCoords([] as any)).toBeNull();
    expect(extractBoundingBoxCoords([1, 2] as any)).toBeNull();
  });

  it('transforms normalized bounding box [0..1] into viewport pixels', () => {
    const box = transformBoundingBox(
      { x0: 0.1, y0: 0.2, x1: 0.5, y1: 0.6 },
      1000,
      800
    );

    expect(box).toEqual({
      x: 100,
      y: 160,
      width: 400,
      height: 320,
    });
  });

  it('transforms unscaled PDF points into viewport pixels using page dimensions', () => {
    // 72 DPI PDF page 612 x 792 displayed at 1224 x 1584 (2x scale)
    const box = transformBoundingBox(
      [72, 100, 216, 300],
      1224,
      1584,
      612,
      792
    );

    expect(box).toEqual({
      x: 144,
      y: 200,
      width: 288,
      height: 400,
    });
  });

  it('clamps coordinates strictly within viewport boundaries', () => {
    const box = transformBoundingBox(
      { x0: -0.2, y0: -0.1, x1: 1.5, y1: 1.2 },
      500,
      400
    );

    expect(box).not.toBeNull();
    expect(box!.x).toBeGreaterThanOrEqual(0);
    expect(box!.y).toBeGreaterThanOrEqual(0);
    expect(box!.x + box!.width).toBeLessThanOrEqual(500);
    expect(box!.y + box!.height).toBeLessThanOrEqual(400);
  });

  it('returns null when viewport dimensions are non-positive', () => {
    expect(transformBoundingBox([0.1, 0.2, 0.3, 0.4], 0, 800)).toBeNull();
    expect(transformBoundingBox([0.1, 0.2, 0.3, 0.4], 800, -10)).toBeNull();
  });
});
