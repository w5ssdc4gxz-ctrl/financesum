import React from 'react';
import { BROWSER, APP_COLORS } from './constants';
import { FONT_FAMILY } from '../constants';

export interface CameraAngle {
  rotateX: number; // tilt forward/back (deg)
  rotateY: number; // turn left/right (deg)
  rotateZ: number; // roll (deg)
  scale?: number; // overall scale (default 0.88)
  translateX?: number; // horizontal offset px
  translateY?: number; // vertical offset px
}

interface BrowserFrameProps {
  url: string;
  children: React.ReactNode;
  /** 3D camera angle for commercial-quality perspective */
  camera?: CameraAngle;
  /** Whether to show a subtle reflection/glow beneath the frame */
  showReflection?: boolean;
}

export const BrowserFrame: React.FC<BrowserFrameProps> = ({
  url,
  children,
  camera = { rotateX: 0, rotateY: 0, rotateZ: 0 },
  showReflection = true,
}) => {
  const {
    rotateX,
    rotateY,
    rotateZ,
    scale = 0.88,
    translateX = 0,
    translateY = 0,
  } = camera;

  return (
    <div
      style={{
        position: 'absolute',
        inset: 0,
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        perspective: 1800,
        perspectiveOrigin: '50% 45%',
      }}
    >
      {/* 3D transformed browser container */}
      <div
        style={{
          width: 1920 - BROWSER.padding * 2,
          height: 1080 - BROWSER.padding * 2,
          borderRadius: BROWSER.borderRadius,
          overflow: 'hidden',
          border: '1px solid rgba(255,255,255,0.08)',
          display: 'flex',
          flexDirection: 'column',
          boxShadow: [
            '0 25px 80px rgba(0,0,0,0.55)',
            '0 10px 30px rgba(0,0,0,0.3)',
            '0 0 0 1px rgba(255,255,255,0.05)',
          ].join(', '),
          transform: [
            `rotateX(${rotateX}deg)`,
            `rotateY(${rotateY}deg)`,
            `rotateZ(${rotateZ}deg)`,
            `scale(${scale})`,
            `translateX(${translateX}px)`,
            `translateY(${translateY}px)`,
          ].join(' '),
          transformStyle: 'preserve-3d',
          backfaceVisibility: 'hidden',
          willChange: 'transform',
        }}
      >
        {/* Browser chrome top bar */}
        <div
          style={{
            height: BROWSER.topBarHeight,
            background: 'linear-gradient(180deg, #2a2a2a 0%, #1c1c1c 100%)',
            display: 'flex',
            alignItems: 'center',
            padding: '0 16px',
            gap: 12,
            borderBottom: '1px solid rgba(255,255,255,0.06)',
            flexShrink: 0,
          }}
        >
          {/* Traffic lights */}
          <div style={{ display: 'flex', gap: 8, marginRight: 8 }}>
            {[
              { bg: '#ff5f57', border: '#e0443e' },
              { bg: '#febc2e', border: '#dea123' },
              { bg: '#28c840', border: '#1aab29' },
            ].map((dot, i) => (
              <div
                key={i}
                style={{
                  width: 12,
                  height: 12,
                  borderRadius: '50%',
                  background: dot.bg,
                  boxShadow: `inset 0 -1px 1px rgba(0,0,0,0.15), 0 0 0 0.5px ${dot.border}`,
                }}
              />
            ))}
          </div>

          {/* URL bar */}
          <div
            style={{
              flex: 1,
              height: 26,
              background: '#0a0a0a',
              borderRadius: 6,
              display: 'flex',
              alignItems: 'center',
              padding: '0 12px',
              gap: 6,
              border: '1px solid rgba(255,255,255,0.06)',
            }}
          >
            {/* Lock icon */}
            <svg width="10" height="12" viewBox="0 0 10 12" fill="none">
              <rect
                x="0.5"
                y="5"
                width="9"
                height="6.5"
                rx="1.5"
                stroke="#666"
                strokeWidth="1"
              />
              <path
                d="M2.5 5V3.5C2.5 2.12 3.62 1 5 1C6.38 1 7.5 2.12 7.5 3.5V5"
                stroke="#666"
                strokeWidth="1"
                fill="none"
              />
            </svg>
            <span
              style={{
                fontSize: 12,
                color: '#888',
                fontFamily: FONT_FAMILY,
                letterSpacing: 0.3,
              }}
            >
              {url}
            </span>
          </div>

          {/* Tab-like indicators on the right */}
          <div style={{ display: 'flex', gap: 6, marginLeft: 8 }}>
            <div
              style={{
                width: 16,
                height: 16,
                borderRadius: 4,
                background: 'rgba(255,255,255,0.05)',
                border: '1px solid rgba(255,255,255,0.08)',
              }}
            />
            <div
              style={{
                width: 16,
                height: 16,
                borderRadius: 4,
                background: 'rgba(255,255,255,0.05)',
                border: '1px solid rgba(255,255,255,0.08)',
              }}
            />
          </div>
        </div>

        {/* Content area */}
        <div
          style={{
            flex: 1,
            position: 'relative',
            overflow: 'hidden',
            background: APP_COLORS.bgDark,
          }}
        >
          {children}
        </div>
      </div>

      {/* Subtle reflection/glow beneath the browser */}
      {showReflection && (
        <div
          style={{
            position: 'absolute',
            bottom: -60,
            left: '15%',
            right: '15%',
            height: 120,
            background:
              'radial-gradient(ellipse at 50% 0%, rgba(0,21,255,0.08) 0%, transparent 70%)',
            filter: 'blur(30px)',
            pointerEvents: 'none',
          }}
        />
      )}
    </div>
  );
};
