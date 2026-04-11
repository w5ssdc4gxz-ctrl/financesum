// Walkthrough video constants
import { COLORS, FPS, FONT_FAMILY } from '../constants';
import type { CameraAngle } from './BrowserFrame';

export { COLORS, FPS, FONT_FAMILY };

// Scene durations in seconds for walkthrough
export const WT_DURATIONS = {
  landing: 4, // Landing page hero -> click "Start Analyzing"
  search: 5, // Dashboard search -> type "AAPL" -> select Apple
  companyPage: 4, // Company page loads -> click "Custom" wizard button
  wizard: 10, // Wizard steps (customize, health, persona, review -> click Complete)
  generating: 5, // Generation loading with progress bar
  results: 11, // Results display with deep scroll animation (expanded)
} as const;

// Total video duration
export const WT_TOTAL_SECONDS = Object.values(WT_DURATIONS).reduce(
  (a, b) => a + b,
  0,
);
export const WT_TOTAL_FRAMES = WT_TOTAL_SECONDS * FPS;

// App design tokens matching the real FinanceSum app
export const APP_COLORS = {
  // Landing page (dark theme)
  primaryBlue: '#0015ff',
  primaryBlueLight: '#4D5EFF',
  bgDark: '#0a0a0a',
  bgCard: '#111111',
  bgGray: '#1a1a1a',
  border: '#2a2a2a',
  borderLight: '#3a3a3a',
  textWhite: '#ffffff',
  textGray: '#999999',
  textMuted: '#666666',
  // Neo-brutalist company page (light theme)
  brutalBorder: '#000000',
  brutalShadow: '4px 4px 0px 0px rgba(0,0,0,1)',
  brutalShadowLarge: '8px 8px 0px 0px rgba(0,0,0,1)',
  brutalShadowSmall: '2px 2px 0px 0px rgba(0,0,0,1)',
  brutalBg: '#ffffff',
  brutalText: '#000000',
  // Health score colors
  healthGreen: '#4ade80', // green-400
  healthBlue: '#60a5fa', // blue-400
  healthYellow: '#facc15', // yellow-400
  healthRed: '#f87171', // red-400
  // Wizard
  wizardGreen: '#22c55e', // green-500
  wizardRed: '#ef4444',
  // Results (Notion-like clean style)
  resultsBg: '#ffffff',
  resultsBorder: 'rgba(148,163,184,0.2)', // slate-200/80
  resultsText: '#374151', // gray-700
  resultsHeading: '#111827', // gray-900
  emerald50: '#ecfdf5',
  emerald600: '#059669',
  red50: '#fef2f2',
  red500: '#ef4444',
  // Action button colors
  actionSave: '#4ade80', // green-400
  actionCopy: '#fde047', // yellow-300
  actionPdf: '#93c5fd', // blue-300
  actionDismiss: '#fb7185', // rose-400
  // Chart colors
  chartBlue: '#3b82f6',
  chartGreen: '#22c55e',
  chartPurple: '#a855f7',
  chartAmber: '#f59e0b',
} as const;

// Browser chrome dimensions
export const BROWSER = {
  topBarHeight: 40,
  borderRadius: 12,
  padding: 40, // padding around browser frame within 1920x1080
} as const;

// Content area dimensions (inside browser frame)
export const CONTENT = {
  width: 1920 - BROWSER.padding * 2,
  height: 1080 - BROWSER.padding * 2 - BROWSER.topBarHeight,
} as const;

// ------------------------------------------------------------------
// Camera angle presets for each scene
// Each scene has a "from" and "to" angle that animate smoothly
// ------------------------------------------------------------------
export const CAMERA_PRESETS: Record<
  string,
  { from: CameraAngle; to: CameraAngle }
> = {
  landing: {
    from: { rotateX: 8, rotateY: -12, rotateZ: 1, scale: 0.85, translateY: 10 },
    to: { rotateX: 4, rotateY: -6, rotateZ: 0.5, scale: 0.88, translateY: 0 },
  },
  search: {
    from: { rotateX: 6, rotateY: 10, rotateZ: -0.5, scale: 0.86, translateX: -20 },
    to: { rotateX: 3, rotateY: 5, rotateZ: 0, scale: 0.89, translateX: -5 },
  },
  companyPage: {
    from: { rotateX: 5, rotateY: -8, rotateZ: 0.8, scale: 0.87, translateX: 15 },
    to: { rotateX: 2, rotateY: -3, rotateZ: 0.3, scale: 0.90, translateX: 5 },
  },
  wizard: {
    from: { rotateX: 7, rotateY: 6, rotateZ: -0.5, scale: 0.85 },
    to: { rotateX: 3, rotateY: 2, rotateZ: 0, scale: 0.89 },
  },
  generating: {
    from: { rotateX: 4, rotateY: -5, rotateZ: 0.3, scale: 0.88 },
    to: { rotateX: 2, rotateY: -2, rotateZ: 0, scale: 0.91 },
  },
  results: {
    from: { rotateX: 6, rotateY: 8, rotateZ: -0.4, scale: 0.84, translateX: -10 },
    to: { rotateX: 2, rotateY: 3, rotateZ: 0, scale: 0.88, translateX: 0 },
  },
} as const;

/**
 * Interpolate between two camera angles based on progress (0-1).
 * Used in each scene to smoothly animate the 3D perspective.
 */
export function lerpCamera(
  from: CameraAngle,
  to: CameraAngle,
  progress: number,
): CameraAngle {
  const t = Math.max(0, Math.min(1, progress));
  return {
    rotateX: from.rotateX + (to.rotateX - from.rotateX) * t,
    rotateY: from.rotateY + (to.rotateY - from.rotateY) * t,
    rotateZ: from.rotateZ + (to.rotateZ - from.rotateZ) * t,
    scale:
      (from.scale ?? 0.88) +
      ((to.scale ?? 0.88) - (from.scale ?? 0.88)) * t,
    translateX:
      (from.translateX ?? 0) +
      ((to.translateX ?? 0) - (from.translateX ?? 0)) * t,
    translateY:
      (from.translateY ?? 0) +
      ((to.translateY ?? 0) - (from.translateY ?? 0)) * t,
  };
}
