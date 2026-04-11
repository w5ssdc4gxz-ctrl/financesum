import React from 'react';
import { interpolate, useCurrentFrame } from 'remotion';

export interface CursorPoint {
  x: number;
  y: number;
  frame: number; // absolute frame when cursor should be at this point
}

interface CursorProps {
  /** Array of waypoints the cursor follows */
  path: CursorPoint[];
  /** Frames at which cursor "clicks" (shows click animation) */
  clickFrames?: number[];
  /** Whether to show cursor */
  visible?: boolean;
}

/**
 * Attempt cubic bezier-like interpolation for natural cursor movement.
 * For each segment between two waypoints, we apply a custom ease curve
 * that accelerates then decelerates, mimicking real hand movement.
 */
function cubicEase(t: number): number {
  // Custom bezier-like ease: fast start, smooth deceleration
  // Approximates cubic-bezier(0.25, 0.1, 0.25, 1.0)
  if (t <= 0) return 0;
  if (t >= 1) return 1;
  return t < 0.5
    ? 4 * t * t * t
    : 1 - Math.pow(-2 * t + 2, 3) / 2;
}

/**
 * Add a slight curve to the path (not perfectly straight lines).
 * This creates a subtle arc between points, like a real hand moving a mouse.
 */
function getCurvedPosition(
  x0: number,
  y0: number,
  x1: number,
  y1: number,
  t: number,
): { x: number; y: number } {
  const eased = cubicEase(t);

  // Add subtle perpendicular offset for arc (maxes at t=0.5)
  const dx = x1 - x0;
  const dy = y1 - y0;
  const dist = Math.sqrt(dx * dx + dy * dy);

  // Perpendicular direction (rotated 90 deg)
  const px = -dy / (dist || 1);
  const py = dx / (dist || 1);

  // Arc intensity: subtle, proportional to distance, max ~15px
  const arcAmount = Math.min(dist * 0.06, 15);
  const arcOffset = arcAmount * Math.sin(eased * Math.PI); // peaks at midpoint

  return {
    x: x0 + (x1 - x0) * eased + px * arcOffset,
    y: y0 + (y1 - y0) * eased + py * arcOffset,
  };
}

export const Cursor: React.FC<CursorProps> = ({
  path,
  clickFrames = [],
  visible = true,
}) => {
  const frame = useCurrentFrame();

  if (!visible || path.length === 0) return null;

  // Find current position by interpolating between waypoints
  let x = path[0].x;
  let y = path[0].y;

  if (frame <= path[0].frame) {
    x = path[0].x;
    y = path[0].y;
  } else if (frame >= path[path.length - 1].frame) {
    x = path[path.length - 1].x;
    y = path[path.length - 1].y;
  } else {
    // Find the segment we're in
    for (let i = 0; i < path.length - 1; i++) {
      if (frame >= path[i].frame && frame <= path[i + 1].frame) {
        const segmentLength = path[i + 1].frame - path[i].frame;
        const t = segmentLength > 0
          ? (frame - path[i].frame) / segmentLength
          : 1;

        const pos = getCurvedPosition(
          path[i].x,
          path[i].y,
          path[i + 1].x,
          path[i + 1].y,
          t,
        );
        x = pos.x;
        y = pos.y;
        break;
      }
    }
  }

  // Click animation state
  let isClicking = false;
  let clickProgress = 0;
  const clickDuration = 10; // frames

  for (const cf of clickFrames) {
    if (frame >= cf && frame <= cf + clickDuration) {
      isClicking = true;
      clickProgress = interpolate(
        frame,
        [cf, cf + clickDuration * 0.3, cf + clickDuration],
        [0, 1, 0],
        { extrapolateLeft: 'clamp', extrapolateRight: 'clamp' },
      );
      break;
    }
  }

  // Cursor press-down effect
  const scale = isClicking ? 1 - clickProgress * 0.12 : 1;

  // Double ripple for richer click feedback
  const ring1Opacity = isClicking ? clickProgress * 0.5 : 0;
  const ring1Scale = isClicking ? 1 + clickProgress * 2 : 1;
  const ring2Opacity = isClicking
    ? interpolate(
        frame - (clickFrames.find((cf) => frame >= cf && frame <= cf + clickDuration) || 0),
        [2, 5, clickDuration],
        [0, 0.3, 0],
        { extrapolateLeft: 'clamp', extrapolateRight: 'clamp' },
      )
    : 0;
  const ring2Scale = isClicking ? 1 + clickProgress * 3 : 1;

  return (
    <div
      style={{
        position: 'absolute',
        left: x,
        top: y,
        zIndex: 9999,
        pointerEvents: 'none',
        transform: `translate(-2px, -1px) scale(${scale})`,
        filter: 'drop-shadow(0 3px 6px rgba(0,0,0,0.4))',
        transition: 'none',
      }}
    >
      {/* Outer ripple ring */}
      {isClicking && (
        <div
          style={{
            position: 'absolute',
            left: 2,
            top: 1,
            width: 24,
            height: 24,
            borderRadius: '50%',
            border: '1.5px solid rgba(0,21,255,0.6)',
            opacity: ring2Opacity,
            transform: `translate(-12px, -12px) scale(${ring2Scale})`,
          }}
        />
      )}

      {/* Inner ripple ring */}
      {isClicking && (
        <div
          style={{
            position: 'absolute',
            left: 2,
            top: 1,
            width: 18,
            height: 18,
            borderRadius: '50%',
            border: '2px solid rgba(255,255,255,0.7)',
            opacity: ring1Opacity,
            transform: `translate(-9px, -9px) scale(${ring1Scale})`,
          }}
        />
      )}

      {/* Click dot flash */}
      {isClicking && clickProgress > 0.3 && (
        <div
          style={{
            position: 'absolute',
            left: 2,
            top: 1,
            width: 6,
            height: 6,
            borderRadius: '50%',
            background: 'rgba(255,255,255,0.9)',
            opacity: interpolate(clickProgress, [0.3, 0.5, 1], [0, 0.8, 0], {
              extrapolateLeft: 'clamp',
              extrapolateRight: 'clamp',
            }),
            transform: 'translate(-3px, -3px)',
          }}
        />
      )}

      {/* Cursor SVG - macOS style pointer */}
      <svg width="24" height="28" viewBox="0 0 24 28" fill="none">
        <path
          d="M3 1L3 20.5L8.5 15.5L13 24L17 22L12.5 13.5L20 13.5L3 1Z"
          fill="white"
          stroke="black"
          strokeWidth="1.5"
          strokeLinejoin="round"
        />
      </svg>
    </div>
  );
};
