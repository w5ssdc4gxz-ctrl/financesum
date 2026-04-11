import React from 'react';
import {
  AbsoluteFill,
  Img,
  interpolate,
  spring,
  staticFile,
  useCurrentFrame,
  useVideoConfig,
} from 'remotion';
import { BrowserFrame } from '../BrowserFrame';
import { APP_COLORS, FPS, FONT_FAMILY, CAMERA_PRESETS, lerpCamera } from '../constants';

/**
 * Scene 6: Results Display (EXPANDED)
 * Shows the AI-generated brief with health score, detailed executive summary,
 * financial metrics, revenue breakdown, balance sheet highlights, cash flow,
 * competitive moat, valuation metrics, Warren Buffett's full analysis,
 * outlook & guidance, and risk factors. Notion-like clean design.
 * Auto-scrolls down to showcase all content.
 * Duration: 11s (330 frames at 30fps)
 */

const clamp = { extrapolateLeft: 'clamp' as const, extrapolateRight: 'clamp' as const };

// Financial metrics data
const KEY_METRICS = [
  { label: 'Revenue', value: '$394.3B', change: '+8.2%', positive: true },
  { label: 'Net Income', value: '$97.0B', change: '+10.5%', positive: true },
  { label: 'EPS (Diluted)', value: '$6.13', change: '+9.7%', positive: true },
  { label: 'Free Cash Flow', value: '$111.4B', change: '+12.1%', positive: true },
  { label: 'Gross Margin', value: '46.2%', change: '+1.4pp', positive: true },
  { label: 'Operating Margin', value: '30.7%', change: '-0.8pp', positive: false },
];

// Revenue breakdown by segment
const REVENUE_SEGMENTS = [
  { segment: 'iPhone', value: '$201.2B', pct: '51%', barWidth: 51 },
  { segment: 'Services', value: '$85.2B', pct: '22%', barWidth: 22 },
  { segment: 'Mac', value: '$29.4B', pct: '7%', barWidth: 7 },
  { segment: 'iPad', value: '$28.3B', pct: '7%', barWidth: 7 },
  { segment: 'Wearables & Home', value: '$39.8B', pct: '10%', barWidth: 10 },
];

// Balance sheet highlights
const BALANCE_SHEET = [
  { label: 'Total Assets', value: '$352.6B' },
  { label: 'Total Liabilities', value: '$290.4B' },
  { label: 'Shareholders\' Equity', value: '$62.1B' },
  { label: 'Cash & Equivalents', value: '$29.9B' },
  { label: 'Total Debt', value: '$108.0B' },
  { label: 'Net Cash Position', value: '-$78.1B' },
];

// Valuation metrics
const VALUATION_METRICS = [
  { label: 'P/E Ratio (TTM)', value: '31.2x', benchmark: 'S&P 500: 22.4x' },
  { label: 'P/S Ratio', value: '8.7x', benchmark: 'Tech avg: 6.2x' },
  { label: 'EV/EBITDA', value: '25.8x', benchmark: 'Tech avg: 20.1x' },
  { label: 'Price/FCF', value: '27.4x', benchmark: 'S&P 500: 18.9x' },
  { label: 'Dividend Yield', value: '0.52%', benchmark: 'S&P 500: 1.4%' },
  { label: 'PEG Ratio', value: '2.8x', benchmark: 'Fair value: 1.0-2.0x' },
];

// Risk factors
const RISK_FACTORS = [
  'Regulatory scrutiny in EU (Digital Markets Act) and US antitrust investigations could fundamentally alter App Store economics and services revenue model',
  'Increasing dependence on services revenue for growth while hardware segments face market saturation in developed markets',
  'Geopolitical risks concentrated in China — both as a critical manufacturing hub and a major end-market representing 19% of total revenue',
  'Competitive pressure from Android ecosystem in emerging markets where price sensitivity limits iPhone adoption',
  'Currency headwinds from strong US dollar negatively impacting international revenue, which constitutes ~58% of total sales',
  'AI/ML capabilities lag behind competitors in certain areas, creating risk as AI becomes a key differentiator in consumer electronics',
];

// Section heading style helper
const sectionHeadingStyle = {
  fontSize: 20,
  fontWeight: 600,
  color: APP_COLORS.resultsHeading,
  fontFamily: FONT_FAMILY,
  marginTop: 0,
  marginBottom: 16,
  paddingBottom: 12,
  borderBottom: '1px solid #e5e5e5',
} as const;

export const WTResultsScene: React.FC = () => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();

  // ── 3D Camera ──────────────────────────────────────────────────
  const cameraProgress = interpolate(frame, [0, 330], [0, 1], clamp);
  const camera = lerpCamera(
    CAMERA_PRESETS.results.from,
    CAMERA_PRESETS.results.to,
    cameraProgress,
  );

  // ── Content fade in with upward motion ─────────────────────────
  const contentOpacity = interpolate(frame, [0, 20], [0, 1], clamp);
  const contentSlide = interpolate(frame, [0, 20], [20, 0], clamp);

  // ── Auto-scroll — deep scroll to show all 11 sections (~3300px content, ~864px visible) ──
  const scrollY = interpolate(frame, [40, 300], [0, -2600], clamp);

  // ── Health score counter ───────────────────────────────────────
  const healthScore = Math.round(
    interpolate(frame, [10, 40], [0, 87], clamp),
  );

  // ── Scene fade out ─────────────────────────────────────────────
  const fadeOut = interpolate(frame, [310, 330], [1, 0], clamp);

  // ── Section stagger helpers ────────────────────────────────────
  const sectionOpacity = (delay: number) =>
    spring({ frame: frame - delay, fps, config: { damping: 20 } });

  const sectionSlide = (progress: number) =>
    interpolate(progress, [0, 1], [12, 0]);

  // Section spring values
  const execSummaryProgress = sectionOpacity(5);
  const metricsProgress = sectionOpacity(15);
  const revenueProgress = sectionOpacity(25);
  const balanceSheetProgress = sectionOpacity(30);
  const cashFlowProgress = sectionOpacity(35);
  const moatProgress = sectionOpacity(40);
  const valuationProgress = sectionOpacity(45);
  const buffettProgress = sectionOpacity(50);
  const outlookProgress = sectionOpacity(55);
  const riskProgress = sectionOpacity(60);

  return (
    <AbsoluteFill style={{ backgroundColor: '#050505', opacity: fadeOut }}>
      <BrowserFrame url="financesums.com/brief/apple-inc-10k" camera={camera}>
        <div
          style={{
            width: '100%',
            height: '100%',
            background: '#ffffff',
            display: 'flex',
            flexDirection: 'column',
            overflow: 'hidden',
          }}
        >
          {/* ── App Top Bar (inside browser, not browser chrome) ── */}
          <div
            style={{
              height: 56,
              background: '#ffffff',
              borderBottom: '1px solid #e5e5e5',
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'space-between',
              padding: '0 32px',
              flexShrink: 0,
            }}
          >
            {/* Left: Logo + text */}
            <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
              <Img
                src={staticFile('logo.png')}
                style={{ width: 24, height: 24, borderRadius: 5 }}
              />
              <span
                style={{
                  fontSize: 16,
                  fontWeight: 800,
                  color: '#000',
                  fontFamily: FONT_FAMILY,
                  textTransform: 'uppercase',
                  letterSpacing: 1,
                }}
              >
                FinanceSum
              </span>
            </div>

            {/* Right: Action buttons (neo-brutalist style) */}
            <div style={{ display: 'flex', gap: 10 }}>
              {[
                { label: 'Save', bg: APP_COLORS.actionSave },
                { label: 'Copy', bg: APP_COLORS.actionCopy },
                { label: 'PDF', bg: APP_COLORS.actionPdf },
              ].map((btn, i) => (
                <div
                  key={i}
                  style={{
                    fontSize: 10,
                    fontWeight: 700,
                    fontFamily: FONT_FAMILY,
                    textTransform: 'uppercase',
                    letterSpacing: 0.5,
                    padding: '6px 12px',
                    background: btn.bg,
                    border: '2px solid #000',
                    boxShadow: '2px 2px 0px 0px rgba(0,0,0,1)',
                    color: '#000',
                    cursor: 'pointer',
                  }}
                >
                  {btn.label}
                </div>
              ))}
            </div>
          </div>

          {/* ── Content Area ─────────────────────────────────────── */}
          <div
            style={{
              flex: 1,
              background: '#ffffff',
              padding: '40px 60px',
              overflow: 'hidden',
              opacity: contentOpacity,
              position: 'relative',
            }}
          >
            {/* Scrollable content wrapper */}
            <div
              style={{
                transform: `translateY(${scrollY + contentSlide}px)`,
              }}
            >
              {/* ── 1. Health Score Badge ───────────────────────── */}
              <div
                style={{
                  display: 'inline-flex',
                  alignItems: 'center',
                  gap: 12,
                  border: '2px solid #000',
                  boxShadow: '2px 2px 0px 0px rgba(0,0,0,1)',
                  background: APP_COLORS.healthGreen,
                  padding: '8px 16px',
                  marginBottom: 24,
                }}
              >
                <span
                  style={{
                    fontSize: 28,
                    fontWeight: 900,
                    fontFamily: FONT_FAMILY,
                    color: '#000',
                    fontVariantNumeric: 'tabular-nums',
                    lineHeight: 1,
                  }}
                >
                  {healthScore}
                </span>
                <span
                  style={{
                    fontSize: 10,
                    fontWeight: 700,
                    fontFamily: FONT_FAMILY,
                    textTransform: 'uppercase',
                    letterSpacing: 1,
                    color: '#000',
                  }}
                >
                  HEALTH SCORE
                </span>
              </div>

              {/* Brief title */}
              <h1
                style={{
                  fontSize: 28,
                  fontWeight: 700,
                  color: APP_COLORS.resultsHeading,
                  fontFamily: FONT_FAMILY,
                  margin: '0 0 8px 0',
                }}
              >
                Apple Inc. (AAPL) — 10-K Annual Report Analysis
              </h1>
              <div
                style={{
                  fontSize: 13,
                  color: '#9ca3af',
                  fontFamily: FONT_FAMILY,
                  marginBottom: 32,
                  display: 'flex',
                  gap: 16,
                }}
              >
                <span>Filed: November 1, 2024</span>
                <span>|</span>
                <span>Fiscal Year Ending: September 28, 2024</span>
                <span>|</span>
                <span>Persona: Warren Buffett</span>
              </div>

              {/* ── 2. Executive Summary ───────────────────────── */}
              <div
                style={{
                  marginBottom: 36,
                  opacity: execSummaryProgress,
                  transform: `translateY(${sectionSlide(execSummaryProgress)}px)`,
                }}
              >
                <h2 style={sectionHeadingStyle}>
                  Executive Summary
                </h2>
                <p
                  style={{
                    fontSize: 15,
                    lineHeight: 1.7,
                    color: APP_COLORS.resultsText,
                    fontFamily: FONT_FAMILY,
                    margin: '0 0 14px 0',
                  }}
                >
                  Apple Inc. continues to demonstrate exceptional operational
                  excellence with record-breaking services revenue of $85.2B
                  (+14.2% YoY) offsetting modest hardware growth across its
                  product lineup. The company&apos;s ecosystem moat remains
                  formidable, with over 2.2 billion active devices creating
                  recurring revenue streams and powerful network effects that
                  would make any value investor proud.
                </p>
                <p
                  style={{
                    fontSize: 15,
                    lineHeight: 1.7,
                    color: APP_COLORS.resultsText,
                    fontFamily: FONT_FAMILY,
                    margin: '0 0 14px 0',
                  }}
                >
                  Total revenue reached $394.3B, representing an 8.2% increase
                  from the prior fiscal year. Notably, the company achieved this
                  growth while simultaneously expanding gross margins to 46.2%,
                  reflecting Apple&apos;s pricing power and favorable shift toward
                  higher-margin services. The Services segment alone now
                  represents 22% of total revenue, up from 19% two years ago.
                </p>
                <p
                  style={{
                    fontSize: 15,
                    lineHeight: 1.7,
                    color: APP_COLORS.resultsText,
                    fontFamily: FONT_FAMILY,
                    margin: 0,
                  }}
                >
                  Free cash flow generation remains extraordinary at $111.4B,
                  enabling aggressive capital returns. During FY2024, Apple
                  returned $94.8B to shareholders through share repurchases
                  ($77.5B) and dividends ($15.0B), continuing its track record
                  as the largest capital return program in corporate history.
                </p>
              </div>

              {/* ── 3. Key Financial Metrics ───────────────────── */}
              <div
                style={{
                  marginBottom: 36,
                  opacity: metricsProgress,
                  transform: `translateY(${sectionSlide(metricsProgress)}px)`,
                }}
              >
                <h2 style={sectionHeadingStyle}>
                  Key Financial Metrics
                </h2>
                <div
                  style={{
                    borderRadius: 16,
                    border: `1px solid ${APP_COLORS.resultsBorder}`,
                    overflow: 'hidden',
                    padding: 0,
                  }}
                >
                  {KEY_METRICS.map((metric, i) => {
                    const rowProgress = spring({
                      frame: frame - (20 + i * 5),
                      fps,
                      config: { damping: 18 },
                    });
                    return (
                      <div
                        key={i}
                        style={{
                          display: 'flex',
                          alignItems: 'center',
                          justifyContent: 'space-between',
                          padding: '16px 24px',
                          borderBottom:
                            i < KEY_METRICS.length - 1
                              ? '1px solid #f3f4f6'
                              : 'none',
                          opacity: rowProgress,
                        }}
                      >
                        <span
                          style={{
                            fontSize: 14,
                            color: APP_COLORS.resultsText,
                            fontFamily: FONT_FAMILY,
                          }}
                        >
                          {metric.label}
                        </span>
                        <div
                          style={{
                            display: 'flex',
                            alignItems: 'center',
                            gap: 10,
                          }}
                        >
                          <span
                            style={{
                              fontSize: 14,
                              fontWeight: 500,
                              fontFamily: FONT_FAMILY,
                              fontVariantNumeric: 'tabular-nums',
                              color: APP_COLORS.resultsHeading,
                            }}
                          >
                            {metric.value}
                          </span>
                          <span
                            style={{
                              fontSize: 12,
                              fontFamily: FONT_FAMILY,
                              fontWeight: 500,
                              padding: '2px 8px',
                              borderRadius: 9999,
                              background: metric.positive
                                ? APP_COLORS.emerald50
                                : APP_COLORS.red50,
                              color: metric.positive
                                ? APP_COLORS.emerald600
                                : APP_COLORS.red500,
                            }}
                          >
                            {metric.change}
                          </span>
                        </div>
                      </div>
                    );
                  })}
                </div>
              </div>

              {/* ── 4. Revenue Breakdown by Segment ────────────── */}
              <div
                style={{
                  marginBottom: 36,
                  opacity: revenueProgress,
                  transform: `translateY(${sectionSlide(revenueProgress)}px)`,
                }}
              >
                <h2 style={sectionHeadingStyle}>
                  Revenue Breakdown by Segment
                </h2>
                <div style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
                  {REVENUE_SEGMENTS.map((seg, i) => {
                    const barProgress = spring({
                      frame: frame - (30 + i * 4),
                      fps,
                      config: { damping: 22, stiffness: 120 },
                    });
                    return (
                      <div key={i}>
                        <div
                          style={{
                            display: 'flex',
                            justifyContent: 'space-between',
                            marginBottom: 6,
                            fontFamily: FONT_FAMILY,
                          }}
                        >
                          <span style={{ fontSize: 14, color: APP_COLORS.resultsText, fontWeight: 500 }}>
                            {seg.segment}
                          </span>
                          <span style={{ fontSize: 14, color: APP_COLORS.resultsHeading, fontWeight: 600, fontVariantNumeric: 'tabular-nums' }}>
                            {seg.value} ({seg.pct})
                          </span>
                        </div>
                        <div
                          style={{
                            height: 10,
                            background: '#f3f4f6',
                            borderRadius: 5,
                            overflow: 'hidden',
                          }}
                        >
                          <div
                            style={{
                              height: '100%',
                              width: `${seg.barWidth * barProgress}%`,
                              background: i === 0 ? '#3b82f6' : i === 1 ? '#8b5cf6' : i === 2 ? '#06b6d4' : i === 3 ? '#f59e0b' : '#10b981',
                              borderRadius: 5,
                            }}
                          />
                        </div>
                      </div>
                    );
                  })}
                </div>
              </div>

              {/* ── 5. Balance Sheet Highlights ─────────────────── */}
              <div
                style={{
                  marginBottom: 36,
                  opacity: balanceSheetProgress,
                  transform: `translateY(${sectionSlide(balanceSheetProgress)}px)`,
                }}
              >
                <h2 style={sectionHeadingStyle}>
                  Balance Sheet Highlights
                </h2>
                <div
                  style={{
                    display: 'grid',
                    gridTemplateColumns: '1fr 1fr 1fr',
                    gap: 12,
                  }}
                >
                  {BALANCE_SHEET.map((item, i) => (
                    <div
                      key={i}
                      style={{
                        padding: '16px 20px',
                        borderRadius: 12,
                        border: `1px solid ${APP_COLORS.resultsBorder}`,
                        background: '#fafafa',
                      }}
                    >
                      <div
                        style={{
                          fontSize: 12,
                          color: '#9ca3af',
                          fontFamily: FONT_FAMILY,
                          fontWeight: 500,
                          marginBottom: 6,
                          textTransform: 'uppercase',
                          letterSpacing: 0.5,
                        }}
                      >
                        {item.label}
                      </div>
                      <div
                        style={{
                          fontSize: 18,
                          fontWeight: 600,
                          color: APP_COLORS.resultsHeading,
                          fontFamily: FONT_FAMILY,
                          fontVariantNumeric: 'tabular-nums',
                        }}
                      >
                        {item.value}
                      </div>
                    </div>
                  ))}
                </div>
              </div>

              {/* ── 6. Cash Flow Analysis ──────────────────────── */}
              <div
                style={{
                  marginBottom: 36,
                  opacity: cashFlowProgress,
                  transform: `translateY(${sectionSlide(cashFlowProgress)}px)`,
                }}
              >
                <h2 style={sectionHeadingStyle}>
                  Cash Flow Analysis
                </h2>
                <p
                  style={{
                    fontSize: 15,
                    lineHeight: 1.7,
                    color: APP_COLORS.resultsText,
                    fontFamily: FONT_FAMILY,
                    margin: '0 0 14px 0',
                  }}
                >
                  Apple generated $118.3B in operating cash flow during FY2024,
                  a 13.4% increase from the prior year. Capital expenditures
                  remained disciplined at $9.9B, resulting in free cash flow of
                  $111.4B — an FCF margin of 28.2%. This extraordinary cash
                  generation is a hallmark of Apple&apos;s asset-light business
                  model and reflects the high-margin nature of its product ecosystem.
                </p>
                <div
                  style={{
                    display: 'flex',
                    gap: 16,
                  }}
                >
                  {[
                    { label: 'Operating CF', value: '$118.3B', color: '#22c55e' },
                    { label: 'CapEx', value: '$9.9B', color: '#ef4444' },
                    { label: 'Free Cash Flow', value: '$111.4B', color: '#3b82f6' },
                    { label: 'Buybacks', value: '$77.5B', color: '#a855f7' },
                  ].map((item, i) => (
                    <div
                      key={i}
                      style={{
                        flex: 1,
                        padding: '14px 16px',
                        borderRadius: 12,
                        borderLeft: `4px solid ${item.color}`,
                        background: '#f9fafb',
                      }}
                    >
                      <div
                        style={{
                          fontSize: 11,
                          color: '#9ca3af',
                          fontFamily: FONT_FAMILY,
                          fontWeight: 500,
                          textTransform: 'uppercase',
                          letterSpacing: 0.5,
                          marginBottom: 4,
                        }}
                      >
                        {item.label}
                      </div>
                      <div
                        style={{
                          fontSize: 16,
                          fontWeight: 600,
                          color: APP_COLORS.resultsHeading,
                          fontFamily: FONT_FAMILY,
                          fontVariantNumeric: 'tabular-nums',
                        }}
                      >
                        {item.value}
                      </div>
                    </div>
                  ))}
                </div>
              </div>

              {/* ── 7. Competitive Moat Assessment ─────────────── */}
              <div
                style={{
                  marginBottom: 36,
                  opacity: moatProgress,
                  transform: `translateY(${sectionSlide(moatProgress)}px)`,
                }}
              >
                <h2 style={sectionHeadingStyle}>
                  Competitive Moat Assessment
                </h2>
                <div style={{ display: 'flex', gap: 12 }}>
                  {[
                    { moat: 'Brand Power', strength: 'Very Strong', level: 95 },
                    { moat: 'Switching Costs', strength: 'Very Strong', level: 92 },
                    { moat: 'Network Effects', strength: 'Strong', level: 78 },
                    { moat: 'Cost Advantage', strength: 'Moderate', level: 65 },
                  ].map((item, i) => {
                    const meterProgress = spring({
                      frame: frame - (45 + i * 3),
                      fps,
                      config: { damping: 25, stiffness: 100 },
                    });
                    return (
                      <div
                        key={i}
                        style={{
                          flex: 1,
                          padding: '16px',
                          borderRadius: 12,
                          border: `1px solid ${APP_COLORS.resultsBorder}`,
                          textAlign: 'center' as const,
                        }}
                      >
                        <div
                          style={{
                            fontSize: 13,
                            fontWeight: 600,
                            color: APP_COLORS.resultsHeading,
                            fontFamily: FONT_FAMILY,
                            marginBottom: 8,
                          }}
                        >
                          {item.moat}
                        </div>
                        {/* Circular-ish meter */}
                        <div
                          style={{
                            width: 56,
                            height: 56,
                            borderRadius: '50%',
                            border: '4px solid #f3f4f6',
                            display: 'flex',
                            alignItems: 'center',
                            justifyContent: 'center',
                            margin: '0 auto 8px',
                            position: 'relative',
                          }}
                        >
                          <span
                            style={{
                              fontSize: 16,
                              fontWeight: 700,
                              fontFamily: FONT_FAMILY,
                              color: item.level >= 80 ? APP_COLORS.emerald600 : '#f59e0b',
                              fontVariantNumeric: 'tabular-nums',
                            }}
                          >
                            {Math.round(item.level * meterProgress)}
                          </span>
                        </div>
                        <div
                          style={{
                            fontSize: 11,
                            fontWeight: 500,
                            color: item.level >= 80 ? APP_COLORS.emerald600 : '#f59e0b',
                            fontFamily: FONT_FAMILY,
                          }}
                        >
                          {item.strength}
                        </div>
                      </div>
                    );
                  })}
                </div>
              </div>

              {/* ── 8. Valuation Metrics ───────────────────────── */}
              <div
                style={{
                  marginBottom: 36,
                  opacity: valuationProgress,
                  transform: `translateY(${sectionSlide(valuationProgress)}px)`,
                }}
              >
                <h2 style={sectionHeadingStyle}>
                  Valuation Metrics
                </h2>
                <div
                  style={{
                    borderRadius: 16,
                    border: `1px solid ${APP_COLORS.resultsBorder}`,
                    overflow: 'hidden',
                  }}
                >
                  {VALUATION_METRICS.map((metric, i) => (
                    <div
                      key={i}
                      style={{
                        display: 'flex',
                        alignItems: 'center',
                        justifyContent: 'space-between',
                        padding: '14px 24px',
                        borderBottom:
                          i < VALUATION_METRICS.length - 1
                            ? '1px solid #f3f4f6'
                            : 'none',
                      }}
                    >
                      <span
                        style={{
                          fontSize: 14,
                          color: APP_COLORS.resultsText,
                          fontFamily: FONT_FAMILY,
                        }}
                      >
                        {metric.label}
                      </span>
                      <div
                        style={{
                          display: 'flex',
                          alignItems: 'center',
                          gap: 12,
                        }}
                      >
                        <span
                          style={{
                            fontSize: 14,
                            fontWeight: 600,
                            fontFamily: FONT_FAMILY,
                            fontVariantNumeric: 'tabular-nums',
                            color: APP_COLORS.resultsHeading,
                          }}
                        >
                          {metric.value}
                        </span>
                        <span
                          style={{
                            fontSize: 11,
                            fontFamily: FONT_FAMILY,
                            color: '#9ca3af',
                          }}
                        >
                          {metric.benchmark}
                        </span>
                      </div>
                    </div>
                  ))}
                </div>
              </div>

              {/* ── 9. Warren Buffett's Analysis (Expanded) ───── */}
              <div
                style={{
                  marginBottom: 36,
                  opacity: buffettProgress,
                  transform: `translateY(${sectionSlide(buffettProgress)}px)`,
                }}
              >
                <h2
                  style={{
                    ...sectionHeadingStyle,
                    display: 'flex',
                    alignItems: 'center',
                    gap: 10,
                  }}
                >
                  <Img
                    src={staticFile('investors/warren-buffett.png')}
                    style={{
                      width: 28,
                      height: 28,
                      borderRadius: '50%',
                      objectFit: 'cover',
                    }}
                  />
                  Warren Buffett&apos;s Analysis
                </h2>
                <p
                  style={{
                    fontSize: 15,
                    lineHeight: 1.7,
                    color: APP_COLORS.resultsText,
                    fontFamily: FONT_FAMILY,
                    margin: '0 0 14px 0',
                  }}
                >
                  From the perspective of value investing principles, Apple
                  represents a near-perfect business. The company possesses what
                  I call an &ldquo;economic moat&rdquo; &mdash; a durable
                  competitive advantage that protects it from competition. The
                  switching costs embedded in Apple&apos;s ecosystem, combined
                  with the powerful brand loyalty, create a business that can
                  raise prices without losing customers.
                </p>
                <p
                  style={{
                    fontSize: 15,
                    lineHeight: 1.7,
                    color: APP_COLORS.resultsText,
                    fontFamily: FONT_FAMILY,
                    margin: '0 0 14px 0',
                  }}
                >
                  The capital allocation is exemplary. Management has returned
                  over $600 billion to shareholders through buybacks and
                  dividends while maintaining a fortress balance sheet. This is
                  exactly the kind of shareholder-friendly management I look for.
                  Tim Cook understands that excess capital should be returned to
                  owners, not squandered on empire-building acquisitions.
                </p>
                <p
                  style={{
                    fontSize: 15,
                    lineHeight: 1.7,
                    color: APP_COLORS.resultsText,
                    fontFamily: FONT_FAMILY,
                    margin: '0 0 14px 0',
                  }}
                >
                  What truly excites me about Apple is the transition to services.
                  Services revenue of $85.2B carries significantly higher margins
                  than hardware, and more importantly, it is recurring in nature.
                  When a consumer pays for iCloud storage, Apple Music, or App
                  Store subscriptions, that revenue comes back month after month.
                  This is the kind of predictable cash flow that allows you to
                  sleep well at night as a shareholder.
                </p>
                <p
                  style={{
                    fontSize: 15,
                    lineHeight: 1.7,
                    color: APP_COLORS.resultsText,
                    fontFamily: FONT_FAMILY,
                    margin: 0,
                  }}
                >
                  However, I must note the valuation. At 31x earnings, Apple is
                  not cheap by traditional value metrics. But when you consider
                  the quality of the business &mdash; the brand, the ecosystem,
                  the cash generation &mdash; and the fact that earnings are
                  likely to compound at 10-12% annually, a premium valuation
                  is justified. As I&apos;ve often said, it&apos;s far better
                  to buy a wonderful company at a fair price than a fair company
                  at a wonderful price.
                </p>
              </div>

              {/* ── 10. Outlook & Guidance ─────────────────────── */}
              <div
                style={{
                  marginBottom: 36,
                  opacity: outlookProgress,
                  transform: `translateY(${sectionSlide(outlookProgress)}px)`,
                }}
              >
                <h2 style={sectionHeadingStyle}>
                  Outlook & Forward Guidance
                </h2>
                <p
                  style={{
                    fontSize: 15,
                    lineHeight: 1.7,
                    color: APP_COLORS.resultsText,
                    fontFamily: FONT_FAMILY,
                    margin: '0 0 14px 0',
                  }}
                >
                  Management expects continued mid-single-digit revenue growth
                  in FY2025, driven primarily by the Services segment and the
                  iPhone 16 product cycle featuring Apple Intelligence AI
                  capabilities. The company anticipates gross margins to remain
                  in the 46-47% range, with services margins expected to exceed
                  72%.
                </p>
                <div
                  style={{
                    display: 'flex',
                    gap: 12,
                  }}
                >
                  {[
                    { label: 'Revenue Growth', value: '5-7%', desc: 'FY2025E' },
                    { label: 'Gross Margin', value: '46-47%', desc: 'Guided range' },
                    { label: 'Services Growth', value: '12-15%', desc: 'Continued momentum' },
                    { label: 'CapEx', value: '~$11B', desc: 'AI infrastructure' },
                  ].map((item, i) => (
                    <div
                      key={i}
                      style={{
                        flex: 1,
                        padding: '14px 16px',
                        borderRadius: 12,
                        background: '#f0f9ff',
                        border: '1px solid #bae6fd',
                      }}
                    >
                      <div
                        style={{
                          fontSize: 11,
                          color: '#0369a1',
                          fontFamily: FONT_FAMILY,
                          fontWeight: 500,
                          textTransform: 'uppercase',
                          letterSpacing: 0.5,
                          marginBottom: 4,
                        }}
                      >
                        {item.label}
                      </div>
                      <div
                        style={{
                          fontSize: 18,
                          fontWeight: 700,
                          color: '#0c4a6e',
                          fontFamily: FONT_FAMILY,
                          marginBottom: 2,
                        }}
                      >
                        {item.value}
                      </div>
                      <div
                        style={{
                          fontSize: 11,
                          color: '#7dd3fc',
                          fontFamily: FONT_FAMILY,
                        }}
                      >
                        {item.desc}
                      </div>
                    </div>
                  ))}
                </div>
              </div>

              {/* ── 11. Risk Factors ───────────────────────────── */}
              <div
                style={{
                  marginBottom: 32,
                  opacity: riskProgress,
                  transform: `translateY(${sectionSlide(riskProgress)}px)`,
                }}
              >
                <h2 style={sectionHeadingStyle}>
                  Risk Factors
                </h2>
                <ul
                  style={{
                    margin: 0,
                    padding: 0,
                    listStyle: 'none',
                  }}
                >
                  {RISK_FACTORS.map((risk, i) => (
                    <li
                      key={i}
                      style={{
                        fontSize: 14,
                        color: APP_COLORS.resultsText,
                        fontFamily: FONT_FAMILY,
                        lineHeight: 1.6,
                        marginBottom: 12,
                        display: 'flex',
                        alignItems: 'flex-start',
                        gap: 10,
                      }}
                    >
                      <span
                        style={{
                          display: 'inline-flex',
                          alignItems: 'center',
                          justifyContent: 'center',
                          width: 20,
                          height: 20,
                          borderRadius: '50%',
                          background: APP_COLORS.red50,
                          color: APP_COLORS.red500,
                          fontSize: 11,
                          fontWeight: 700,
                          fontFamily: FONT_FAMILY,
                          flexShrink: 0,
                          marginTop: 2,
                        }}
                      >
                        {i + 1}
                      </span>
                      {risk}
                    </li>
                  ))}
                </ul>
              </div>

              {/* Bottom spacer to ensure scroll reaches end cleanly */}
              <div style={{ height: 80 }} />
            </div>
          </div>
        </div>
      </BrowserFrame>
    </AbsoluteFill>
  );
};
