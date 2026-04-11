// FinanceSum Video Constants
import { loadFont } from '@remotion/google-fonts/Inter';

const { fontFamily } = loadFont('normal', {
  weights: ['400', '600', '700', '800'],
  subsets: ['latin'],
});
export const FONT_FAMILY = fontFamily;

export const COLORS = {
  // Brand gradient
  gradientStart: '#a855f7', // purple
  gradientEnd: '#22d3ee', // cyan
  // Backgrounds
  darkBg: '#0f172a', // slate-900
  darkerBg: '#020617', // slate-950
  cardBg: '#1e293b', // slate-800
  cardBgLight: '#334155', // slate-700
  // Text
  textPrimary: '#f8fafc', // slate-50
  textSecondary: '#94a3b8', // slate-400
  textMuted: '#64748b', // slate-500
  // Accents
  purple: '#a855f7',
  cyan: '#22d3ee',
  green: '#34d399',
  amber: '#fbbf24',
  red: '#f87171',
  blue: '#60a5fa',
  // Pricing
  proPurple: '#7c3aed',
} as const;

export const FPS = 30;

// Scene durations in seconds
export const SCENE_DURATIONS = {
  intro: 4,
  problem: 5,
  solution: 5,
  features: 8,
  personas: 6,
  pipeline: 5,
  pricing: 5,
  cta: 4,
} as const;

// Transition duration in frames
export const TRANSITION_FRAMES = 15;

// Investor personas data
export const INVESTORS = [
  { name: 'Warren Buffett', image: 'investors/warren-buffett.png', style: 'Value Investing' },
  { name: 'Charlie Munger', image: 'investors/charlie-munger.webp', style: 'Mental Models' },
  { name: 'Benjamin Graham', image: 'investors/benjamin-graham.jpg', style: 'Security Analysis' },
  { name: 'Peter Lynch', image: 'investors/peter-lynch.webp', style: 'Growth at Fair Price' },
  { name: 'Ray Dalio', image: 'investors/ray-dalio.webp', style: 'Macro Principles' },
  { name: 'Cathie Wood', image: 'investors/cathie-wood.jpg', style: 'Disruptive Innovation' },
  { name: 'Joel Greenblatt', image: 'investors/joel-greenblatt.jpg', style: 'Magic Formula' },
  { name: 'John Bogle', image: 'investors/john-bogle.jpg', style: 'Index Investing' },
  { name: 'Howard Marks', image: 'investors/howard-marks.jpg', style: 'Risk Assessment' },
  { name: 'Bill Ackman', image: 'investors/bill-ackman.jpg', style: 'Activist Investing' },
] as const;

export const FEATURES = [
  {
    title: 'Customize Your Brief',
    description: 'Pick focus areas, tone, detail level, complexity, and target length.',
    image: 'walkthrough/step-1.png',
  },
  {
    title: 'Health Analysis',
    description: 'Include a detailed Financial Health Rating in your brief.',
    image: 'walkthrough/step-3.png',
  },
  {
    title: 'Investor Persona',
    description: 'Apply a legendary investor lens to every summary.',
    image: 'walkthrough/step-4.png',
  },
  {
    title: 'Generate & Export',
    description: 'One click to PDF or DOCX. Ready in minutes.',
    image: 'walkthrough/step-6.png',
  },
] as const;
