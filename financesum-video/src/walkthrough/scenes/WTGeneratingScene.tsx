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
 * Scene 5: Generation Loading
 * Full-screen loading overlay with progress bar, percentage, ETA,
 * rotating status messages, and animated dots.
 * Duration: 5s (150 frames at 30fps)
 */

const clamp = { extrapolateLeft: 'clamp' as const, extrapolateRight: 'clamp' as const };

export const WTGeneratingScene: React.FC = () => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();

  // ── 3D Camera ──────────────────────────────────────────────────
  const cameraProgress = interpolate(frame, [0, 150], [0, 1], clamp);
  const camera = lerpCamera(
    CAMERA_PRESETS.generating.from,
    CAMERA_PRESETS.generating.to,
    cameraProgress,
  );

  // ── Progress values ────────────────────────────────────────────
  const percent = Math.min(
    98,
    Math.floor(interpolate(frame, [10, 140], [0, 98], clamp)),
  );

  const eta = Math.max(1, 15 - Math.floor(frame / 10));

  // ── Status text rotation ───────────────────────────────────────
  const statusText =
    percent < 25
      ? 'Fetching SEC filing data...'
      : percent < 50
        ? 'Parsing financial statements...'
        : percent < 75
          ? 'Running AI analysis...'
          : 'Generating executive brief...';

  // ── Shimmer on progress bar fill ───────────────────────────────
  const shimmerX = interpolate(frame % 30, [0, 30], [-100, 200], clamp);

  // ── Card entrance animation ────────────────────────────────────
  const cardScale = spring({
    frame,
    fps,
    config: { damping: 14, stiffness: 120, mass: 0.8 },
    from: 0.95,
    to: 1,
    durationInFrames: 20,
  });

  const cardOpacity = interpolate(frame, [0, 15], [0, 1], clamp);

  // ── Scene fade out ─────────────────────────────────────────────
  const fadeOut = interpolate(frame, [130, 150], [1, 0], clamp);

  // ── Dot pulse helper ───────────────────────────────────────────
  const dotOpacity = (index: number) =>
    interpolate(
      ((frame + index * 8) % 24),
      [0, 6, 12, 24],
      [1.0, 0.2, 1.0, 0.2],
      clamp,
    );

  return (
    <AbsoluteFill style={{ backgroundColor: '#050505', opacity: fadeOut }}>
      <BrowserFrame url="financesums.com/company/apple-inc" camera={camera}>
        {/* White page behind the overlay */}
        <div
          style={{
            width: '100%',
            height: '100%',
            background: '#fafafa',
            position: 'relative',
          }}
        >
          {/* Full-area overlay */}
          <div
            style={{
              position: 'absolute',
              inset: 0,
              background: 'rgba(255,255,255,0.92)',
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
            }}
          >
            {/* Centered loading card (neo-brutalist) */}
            <div
              style={{
                width: 480,
                background: '#ffffff',
                border: '2px solid #000000',
                boxShadow: '8px 8px 0px 0px rgba(0,0,0,1)',
                padding: 40,
                display: 'flex',
                flexDirection: 'column',
                alignItems: 'center',
                transform: `scale(${cardScale})`,
                opacity: cardOpacity,
              }}
            >
              {/* 1. Logo */}
              <Img
                src={staticFile('logo.png')}
                style={{
                  width: 40,
                  height: 40,
                  borderRadius: 8,
                  marginBottom: 16,
                }}
              />

              {/* 2. Brand text */}
              <div
                style={{
                  fontSize: 11,
                  fontWeight: 700,
                  fontFamily: FONT_FAMILY,
                  textTransform: 'uppercase',
                  letterSpacing: 3,
                  color: '#999999',
                  marginBottom: 32,
                }}
              >
                FINANCESUM
              </div>

              {/* 3. Animated dots */}
              <div
                style={{
                  display: 'flex',
                  gap: 10,
                  marginBottom: 28,
                }}
              >
                {[0, 1, 2].map((i) => (
                  <div
                    key={i}
                    style={{
                      width: 8,
                      height: 8,
                      borderRadius: '50%',
                      background: '#000000',
                      opacity: dotOpacity(i),
                    }}
                  />
                ))}
              </div>

              {/* 4. Percentage */}
              <div
                style={{
                  fontSize: 42,
                  fontWeight: 900,
                  fontFamily: FONT_FAMILY,
                  color: '#000000',
                  fontVariantNumeric: 'tabular-nums',
                  lineHeight: 1,
                  marginBottom: 8,
                }}
              >
                {percent}%
              </div>

              {/* 5. Status text */}
              <div
                style={{
                  fontSize: 14,
                  fontWeight: 500,
                  fontFamily: FONT_FAMILY,
                  color: '#666666',
                  marginBottom: 24,
                }}
              >
                {statusText}
              </div>

              {/* 6. Progress bar */}
              <div
                style={{
                  width: '100%',
                  height: 12,
                  border: '2px solid #000000',
                  position: 'relative',
                  overflow: 'hidden',
                  marginBottom: 16,
                }}
              >
                {/* Fill */}
                <div
                  style={{
                    width: `${percent}%`,
                    height: '100%',
                    background: '#000000',
                    position: 'relative',
                    overflow: 'hidden',
                  }}
                >
                  {/* Shimmer overlay */}
                  <div
                    style={{
                      position: 'absolute',
                      top: 0,
                      left: 0,
                      width: '100%',
                      height: '100%',
                      background: `linear-gradient(90deg, transparent ${shimmerX}%, rgba(255,255,255,0.3) ${shimmerX + 15}%, transparent ${shimmerX + 30}%)`,
                    }}
                  />
                </div>
              </div>

              {/* 7. ETA text */}
              <div
                style={{
                  fontSize: 12,
                  fontFamily: FONT_FAMILY,
                  color: '#999999',
                }}
              >
                Estimated time: ~{eta}s
              </div>

              {/* 8. Filing info */}
              <div
                style={{
                  fontSize: 12,
                  fontWeight: 600,
                  fontFamily: FONT_FAMILY,
                  color: '#aaaaaa',
                  marginTop: 12,
                  textTransform: 'uppercase',
                }}
              >
                10-K · Apple Inc. (AAPL)
              </div>
            </div>
          </div>
        </div>
      </BrowserFrame>
    </AbsoluteFill>
  );
};
