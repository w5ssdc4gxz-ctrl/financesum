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
import { Cursor } from '../Cursor';
import { APP_COLORS, FPS, FONT_FAMILY, CAMERA_PRESETS, lerpCamera } from '../constants';

/**
 * Scene 7: Export + Outro (final scene)
 * Phase 1 (0-60): Cursor clicks PDF export button, download notification slides in.
 * Phase 2 (60-120): Browser fades/scales away, FinanceSum logo + tagline outro.
 * Duration: 4s (120 frames at 30fps)
 */
export const WTExportScene: React.FC = () => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();

  // ── 3D Camera ──────────────────────────────────────────────
  const cameraProgress = interpolate(frame, [0, 120], [0, 1], {
    extrapolateLeft: 'clamp',
    extrapolateRight: 'clamp',
  });
  const camera = lerpCamera(
    CAMERA_PRESETS.exportOutro.from,
    CAMERA_PRESETS.exportOutro.to,
    cameraProgress,
  );

  // ── Phase 1: PDF Export (frames 0-60) ──────────────────────

  // Cursor click at frame 25
  const isClicked = frame >= 25;

  // PDF button pressed state after click
  const btnPressedTranslate = isClicked ? 2 : 0;
  const btnPressedShadow = isClicked
    ? 'none'
    : '4px 4px 0px 0px rgba(0,0,0,1)';

  // Download notification slides in from bottom-right at frame 35
  const downloadSpring = spring({
    frame: frame - 35,
    fps,
    config: { damping: 14, stiffness: 120 },
  });
  const downloadTranslateY = interpolate(downloadSpring, [0, 1], [80, 0], {
    extrapolateLeft: 'clamp',
    extrapolateRight: 'clamp',
  });
  const downloadOpacity = interpolate(downloadSpring, [0, 1], [0, 1], {
    extrapolateLeft: 'clamp',
    extrapolateRight: 'clamp',
  });

  // ── Phase 2: Outro (frames 60-120) ────────────────────────

  // Browser fades out over frames 55-75
  const browserOpacity = interpolate(frame, [55, 75], [1, 0], {
    extrapolateLeft: 'clamp',
    extrapolateRight: 'clamp',
  });
  const browserScale = interpolate(frame, [55, 75], [1, 0.95], {
    extrapolateLeft: 'clamp',
    extrapolateRight: 'clamp',
  });

  // Logo scales in with spring starting frame 70
  const logoSpring = spring({
    frame: frame - 70,
    fps,
    config: { damping: 14, stiffness: 100 },
  });
  const logoScale = interpolate(logoSpring, [0, 1], [0.5, 1.0], {
    extrapolateLeft: 'clamp',
    extrapolateRight: 'clamp',
  });
  const logoOpacity = interpolate(frame, [65, 80], [0, 1], {
    extrapolateLeft: 'clamp',
    extrapolateRight: 'clamp',
  });

  // "FinanceSum" text
  const titleOpacity = interpolate(frame, [75, 90], [0, 1], {
    extrapolateLeft: 'clamp',
    extrapolateRight: 'clamp',
  });

  // Tagline
  const taglineOpacity = interpolate(frame, [80, 95], [0, 1], {
    extrapolateLeft: 'clamp',
    extrapolateRight: 'clamp',
  });

  // CTA
  const ctaOpacity = interpolate(frame, [85, 100], [0, 1], {
    extrapolateLeft: 'clamp',
    extrapolateRight: 'clamp',
  });

  // ── Scene transition: final fade to black (frames 110-120) ─
  const sceneFade = interpolate(frame, [110, 120], [1, 0], {
    extrapolateLeft: 'clamp',
    extrapolateRight: 'clamp',
  });

  // ── Cursor path ────────────────────────────────────────────
  const cursorPath = [
    { x: 700, y: 200, frame: 5 },
    { x: 1100, y: 72, frame: 20 },
  ];
  const cursorVisible = frame >= 5 && frame <= 55;

  return (
    <AbsoluteFill style={{ backgroundColor: '#050505', opacity: sceneFade }}>
      {/* ─── Browser layer (fades out in Phase 2) ─── */}
      <div
        style={{
          position: 'absolute',
          inset: 0,
          opacity: browserOpacity,
          transform: `scale(${browserScale})`,
        }}
      >
        <BrowserFrame url="financesums.com/company/apple-inc" camera={camera}>
          <div
            style={{
              width: '100%',
              height: '100%',
              background: '#fafafa',
              display: 'flex',
              flexDirection: 'column',
              overflow: 'hidden',
            }}
          >
            {/* Navbar */}
            <div
              style={{
                height: 56,
                background: '#ffffff',
                borderBottom: '2px solid #000',
                display: 'flex',
                alignItems: 'center',
                padding: '0 32px',
                justifyContent: 'space-between',
                flexShrink: 0,
              }}
            >
              {/* Left: logo + name */}
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

              {/* Right: action buttons */}
              <div style={{ display: 'flex', gap: 10, alignItems: 'center' }}>
                {/* Save button */}
                <div
                  style={{
                    padding: '6px 14px',
                    background: APP_COLORS.actionSave,
                    border: '2px solid #000',
                    boxShadow: '4px 4px 0px 0px rgba(0,0,0,1)',
                    fontSize: 12,
                    fontWeight: 700,
                    fontFamily: FONT_FAMILY,
                    textTransform: 'uppercase',
                    letterSpacing: 0.8,
                    display: 'flex',
                    alignItems: 'center',
                    gap: 5,
                    color: '#000',
                  }}
                >
                  <span style={{ fontSize: 14 }}>{'\u2606'}</span>
                  Save
                </div>

                {/* Copy button */}
                <div
                  style={{
                    padding: '6px 14px',
                    background: APP_COLORS.actionCopy,
                    border: '2px solid #000',
                    boxShadow: '4px 4px 0px 0px rgba(0,0,0,1)',
                    fontSize: 12,
                    fontWeight: 700,
                    fontFamily: FONT_FAMILY,
                    textTransform: 'uppercase',
                    letterSpacing: 0.8,
                    display: 'flex',
                    alignItems: 'center',
                    gap: 5,
                    color: '#000',
                  }}
                >
                  <span style={{ fontSize: 14 }}>{'\u2398'}</span>
                  Copy
                </div>

                {/* PDF button (target for cursor click) */}
                <div
                  style={{
                    padding: '6px 14px',
                    background: APP_COLORS.actionPdf,
                    border: '2px solid #000',
                    boxShadow: btnPressedShadow,
                    transform: `translate(${btnPressedTranslate}px, ${btnPressedTranslate}px)`,
                    fontSize: 12,
                    fontWeight: 700,
                    fontFamily: FONT_FAMILY,
                    textTransform: 'uppercase',
                    letterSpacing: 0.8,
                    display: 'flex',
                    alignItems: 'center',
                    gap: 5,
                    color: '#000',
                  }}
                >
                  <span style={{ fontSize: 14 }}>{'\u2193'}</span>
                  PDF
                </div>

                {/* DOCX button */}
                <div
                  style={{
                    padding: '6px 14px',
                    background: '#fff',
                    border: '2px solid #000',
                    boxShadow: '4px 4px 0px 0px rgba(0,0,0,1)',
                    fontSize: 12,
                    fontWeight: 700,
                    fontFamily: FONT_FAMILY,
                    textTransform: 'uppercase',
                    letterSpacing: 0.8,
                    display: 'flex',
                    alignItems: 'center',
                    gap: 5,
                    color: '#000',
                  }}
                >
                  <span style={{ fontSize: 14 }}>{'\u2193'}</span>
                  DOCX
                </div>
              </div>
            </div>

            {/* Results page content area (simplified / blurred placeholder) */}
            <div
              style={{
                flex: 1,
                padding: '32px 48px',
                display: 'flex',
                flexDirection: 'column',
                gap: 20,
              }}
            >
              {/* Summary card header placeholder */}
              <div
                style={{
                  background: '#fff',
                  border: '2px solid #000',
                  boxShadow: APP_COLORS.brutalShadow,
                  overflow: 'hidden',
                }}
              >
                {/* Color bar */}
                <div
                  style={{
                    height: 6,
                    background: `linear-gradient(90deg, ${APP_COLORS.healthGreen}, ${APP_COLORS.chartBlue})`,
                  }}
                />
                {/* Header row */}
                <div
                  style={{
                    padding: '16px 24px',
                    borderBottom: '1px solid #eee',
                    display: 'flex',
                    alignItems: 'center',
                    justifyContent: 'space-between',
                  }}
                >
                  <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
                    <span
                      style={{
                        fontSize: 11,
                        fontWeight: 800,
                        fontFamily: FONT_FAMILY,
                        background: '#dcfce7',
                        color: '#166534',
                        padding: '3px 10px',
                        border: '2px solid #000',
                      }}
                    >
                      10-K
                    </span>
                    <span
                      style={{
                        fontSize: 16,
                        fontWeight: 800,
                        fontFamily: FONT_FAMILY,
                        color: '#000',
                      }}
                    >
                      Apple Inc. — AI Financial Brief
                    </span>
                  </div>
                  <div
                    style={{
                      display: 'flex',
                      alignItems: 'center',
                      gap: 6,
                      border: '2px solid #000',
                      padding: '4px 12px',
                      background: '#f0fdf4',
                    }}
                  >
                    <span
                      style={{
                        fontSize: 10,
                        fontWeight: 700,
                        fontFamily: FONT_FAMILY,
                        color: '#666',
                        textTransform: 'uppercase',
                      }}
                    >
                      Health
                    </span>
                    <span
                      style={{
                        fontSize: 20,
                        fontWeight: 900,
                        fontFamily: FONT_FAMILY,
                        color: APP_COLORS.healthGreen,
                      }}
                    >
                      87
                    </span>
                  </div>
                </div>

                {/* Blurred/placeholder content rows */}
                <div style={{ padding: '20px 24px' }}>
                  {[1, 2, 3, 4, 5].map((_, i) => (
                    <div
                      key={i}
                      style={{
                        height: 14,
                        background: '#f0f0f0',
                        borderRadius: 4,
                        marginBottom: 12,
                        width: `${85 - i * 8}%`,
                      }}
                    />
                  ))}
                  <div style={{ display: 'flex', gap: 16, marginTop: 16 }}>
                    {[1, 2, 3].map((_, i) => (
                      <div
                        key={i}
                        style={{
                          flex: 1,
                          height: 60,
                          background: '#f5f5f5',
                          borderRadius: 6,
                          border: '1px solid #eee',
                        }}
                      />
                    ))}
                  </div>
                  <div style={{ marginTop: 20 }}>
                    {[1, 2, 3].map((_, i) => (
                      <div
                        key={i}
                        style={{
                          height: 10,
                          background: '#f0f0f0',
                          borderRadius: 3,
                          marginBottom: 10,
                          width: `${90 - i * 12}%`,
                        }}
                      />
                    ))}
                  </div>
                </div>
              </div>
            </div>

            {/* Download notification */}
            {frame >= 35 && (
              <div
                style={{
                  position: 'absolute',
                  bottom: 20,
                  right: 20,
                  background: '#ffffff',
                  border: '2px solid #000',
                  boxShadow: '4px 4px 0px 0px rgba(0,0,0,1)',
                  padding: '16px 20px',
                  display: 'flex',
                  gap: 12,
                  alignItems: 'center',
                  opacity: downloadOpacity,
                  transform: `translateY(${downloadTranslateY}px)`,
                }}
              >
                {/* Green checkmark circle */}
                <div
                  style={{
                    width: 24,
                    height: 24,
                    borderRadius: '50%',
                    background: '#4ade80',
                    border: '2px solid #000',
                    display: 'flex',
                    alignItems: 'center',
                    justifyContent: 'center',
                    flexShrink: 0,
                  }}
                >
                  <span
                    style={{
                      color: '#fff',
                      fontWeight: 700,
                      fontSize: 14,
                      lineHeight: 1,
                    }}
                  >
                    {'\u2713'}
                  </span>
                </div>

                {/* Download text */}
                <div style={{ display: 'flex', flexDirection: 'column', gap: 2 }}>
                  <span
                    style={{
                      fontSize: 13,
                      fontWeight: 600,
                      fontFamily: FONT_FAMILY,
                      color: '#000',
                    }}
                  >
                    Apple_AAPL_10K_Brief.pdf
                  </span>
                  <span
                    style={{
                      fontSize: 11,
                      fontFamily: FONT_FAMILY,
                      color: '#666',
                    }}
                  >
                    Downloaded successfully
                  </span>
                </div>
              </div>
            )}
          </div>
        </BrowserFrame>

        {/* Cursor (Phase 1 only) */}
        <Cursor
          path={cursorPath}
          clickFrames={[25]}
          visible={cursorVisible}
        />
      </div>

      {/* ─── Outro overlay (Phase 2) ─── */}
      {frame >= 60 && (
        <AbsoluteFill
          style={{
            backgroundColor: '#050505',
            display: 'flex',
            flexDirection: 'column',
            alignItems: 'center',
            justifyContent: 'center',
          }}
        >
          {/* Logo */}
          <Img
            src={staticFile('logo.png')}
            style={{
              width: 64,
              height: 64,
              borderRadius: 12,
              opacity: logoOpacity,
              transform: `scale(${logoScale})`,
            }}
          />

          {/* "FinanceSum" */}
          <div
            style={{
              fontSize: 36,
              fontWeight: 800,
              fontFamily: FONT_FAMILY,
              color: '#ffffff',
              marginTop: 16,
              opacity: titleOpacity,
            }}
          >
            FinanceSum
          </div>

          {/* Tagline */}
          <div
            style={{
              fontSize: 18,
              fontFamily: FONT_FAMILY,
              color: '#666',
              marginTop: 8,
              opacity: taglineOpacity,
            }}
          >
            Financial analysis, reimagined.
          </div>

          {/* CTA URL */}
          <div
            style={{
              fontSize: 14,
              fontFamily: FONT_FAMILY,
              color: APP_COLORS.primaryBlue,
              fontWeight: 600,
              marginTop: 24,
              opacity: ctaOpacity,
            }}
          >
            financesums.com
          </div>
        </AbsoluteFill>
      )}
    </AbsoluteFill>
  );
};
