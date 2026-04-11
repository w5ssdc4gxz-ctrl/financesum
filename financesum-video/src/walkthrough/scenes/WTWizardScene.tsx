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
 * Scene 4: Summary Wizard (4 steps)
 * Step 1: Customize Output – select focus area chips (frames 0-80)
 * Step 2: Health Analysis – click "Yes, Include It" (frames 80-140)
 * Step 3: Select Persona – click Warren Buffett (frames 140-220)
 * Step 4: Ready to Generate – review & click Complete (frames 220-300)
 * Duration: 10s (300 frames at 30fps)
 *
 * Content area: 1840 × 959 (browser frame minus chrome bar)
 * Modal: 780px wide, height=620 (fixed), border 2px → outer 784×624
 *   Centered: left=(1840-784)/2=528, top=(959-624)/2≈168
 *   Interior: left=530, top=170
 *   Header: 24pad + 51(stepper) + 24pad + 2border = 101px
 *   Body: padding 28px top, 36px sides
 *   Body content origin: x≈566, y≈299
 */

// ---------------------------------------------------------------------------
// Data
// ---------------------------------------------------------------------------

const FOCUS_AREAS = [
  'Financial Performance',
  'Risk Factors',
  'Strategy & Execution',
  'Capital Allocation',
  'Liquidity & Balance Sheet',
  'Guidance & Outlook',
];

const PERSONAS = [
  { name: 'Warren Buffett', style: 'Value Investing', img: 'investors/warren-buffett.png' },
  { name: 'Charlie Munger', style: 'Mental Models', img: 'investors/charlie-munger.webp' },
  { name: 'Benjamin Graham', style: 'Security Analysis', img: 'investors/benjamin-graham.jpg' },
  { name: 'Peter Lynch', style: 'Growth at Fair Price', img: 'investors/peter-lynch.webp' },
  { name: 'Ray Dalio', style: 'Macro Principles', img: 'investors/ray-dalio.webp' },
  { name: 'Cathie Wood', style: 'Disruptive Innovation', img: 'investors/cathie-wood.jpg' },
  { name: 'Joel Greenblatt', style: 'Magic Formula', img: 'investors/joel-greenblatt.jpg' },
  { name: 'Howard Marks', style: 'Risk Assessment', img: 'investors/howard-marks.jpg' },
];

const STEP_LABELS = ['Customize', 'Health', 'Persona', 'Generate'];

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

export const WTWizardScene: React.FC = () => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();

  // -----------------------------------------------------------------------
  // 3D camera
  // -----------------------------------------------------------------------
  const cameraProgress = interpolate(frame, [0, 300], [0, 1], {
    extrapolateLeft: 'clamp',
    extrapolateRight: 'clamp',
  });

  const camera = lerpCamera(
    CAMERA_PRESETS.wizard.from,
    CAMERA_PRESETS.wizard.to,
    cameraProgress,
  );

  // -----------------------------------------------------------------------
  // Current step
  // -----------------------------------------------------------------------
  const currentStep = frame < 80 ? 1 : frame < 140 ? 2 : frame < 220 ? 3 : 4;

  const stepStartFrame =
    currentStep === 1 ? 0 : currentStep === 2 ? 80 : currentStep === 3 ? 140 : 220;
  const localFrame = frame - stepStartFrame;

  // Step content fade-in
  const stepFadeIn = interpolate(localFrame, [0, 12], [0, 1], {
    extrapolateLeft: 'clamp',
    extrapolateRight: 'clamp',
  });

  // Scene fade-out
  const fadeOut = interpolate(frame, [280, 300], [1, 0], {
    extrapolateLeft: 'clamp',
    extrapolateRight: 'clamp',
  });

  // -----------------------------------------------------------------------
  // Focus area selections (Step 1)
  // -----------------------------------------------------------------------
  const selectedFocusAreas = new Set<number>();
  if (currentStep === 1) {
    if (frame >= 25) selectedFocusAreas.add(0); // Financial Performance
    if (frame >= 35) selectedFocusAreas.add(2); // Strategy & Execution
    if (frame >= 45) selectedFocusAreas.add(4); // Liquidity & Balance Sheet
  } else {
    selectedFocusAreas.add(0);
    selectedFocusAreas.add(2);
    selectedFocusAreas.add(4);
  }

  // -----------------------------------------------------------------------
  // Health analysis selection (Step 2)
  // -----------------------------------------------------------------------
  const healthSelected = frame >= 118;

  // -----------------------------------------------------------------------
  // Persona selection (Step 3) — Buffett at frame 178
  // -----------------------------------------------------------------------
  const buffettSelected = frame >= 178;

  // -----------------------------------------------------------------------
  // Complete button (Step 4) — click at frame 260
  // -----------------------------------------------------------------------
  const completeClickFrame = 260;
  const completeClicked = frame >= completeClickFrame;

  // -----------------------------------------------------------------------
  // Cursor path — coordinates relative to content area (1840×959)
  //
  // Modal: 780px wide, height=620 (fixed), border 2px (content-box)
  //   Outer: 784×624, centered → left=528, top≈168
  //   Interior: left=530, top=170
  //   Header=101px, Body padding: 28px top, 36px sides
  //   Body content origin: x≈566, y≈299
  //
  // Step 1 chips: title(14px+20mb=37) + subtitle(13px+16mb=32) → chips at y≈368
  //   Chip height: 2brd+8pad+13txt+8pad+2brd=33px, center row1 y≈384, row2 y≈427
  //   Chip 0 (Financial Performance): first chip in row, center x≈655
  //   Chip 2 (Strategy & Execution): 3rd chip in row, center x≈920
  //   Chip 4 (Liquidity & Balance Sheet): first chip in row2, center x≈660
  //
  // Step 2 buttons: title(14px+12mb=29) + subtitle(13px+24mb=40) → buttons at y≈368
  //   Each button: flex:1, padding:20, border:2 → height≈61, center y≈398
  //   Left button (Yes) center x≈738
  //
  // Step 3 persona grid: title(29) + subtitle(32) → grid at y≈360
  //   4-col grid, each track≈168px. Card height≈110px.
  //   Buffett (col 1) center: x≈650, y≈415
  //
  // Step 4: title(37) + summary list(4×25=100) + mb(24) → button at y≈460
  //   Button height≈49, center y≈484. Full-width center x≈920
  // -----------------------------------------------------------------------
  const cursorPath = [
    // Step 1 – click focus chips
    { x: 800, y: 400, frame: 0 },       // start position
    { x: 655, y: 384, frame: 20 },      // Financial Performance chip
    { x: 920, y: 384, frame: 33 },      // Strategy & Execution chip
    { x: 660, y: 427, frame: 43 },      // Liquidity & Balance Sheet chip
    // Step 2 – click "Yes, Include It"
    { x: 660, y: 427, frame: 80 },      // hold position
    { x: 738, y: 398, frame: 110 },     // Yes button center
    // Step 3 – click Warren Buffett
    { x: 738, y: 398, frame: 140 },     // hold position
    { x: 650, y: 415, frame: 170 },     // Buffett card center
    // Step 4 – click "Complete"
    { x: 650, y: 415, frame: 220 },     // hold position
    { x: 920, y: 484, frame: 250 },     // Complete button center
  ];

  const clickFrames = [25, 37, 47, 118, 178, 260];

  // -----------------------------------------------------------------------
  // Render
  // -----------------------------------------------------------------------
  return (
    <AbsoluteFill style={{ backgroundColor: '#050505', opacity: fadeOut }}>
      <BrowserFrame url="financesums.com/company/apple-inc" camera={camera}>
        <div
          style={{
            width: '100%',
            height: '100%',
            background: '#fafafa',
            position: 'relative',
          }}
        >
          {/* Dark overlay */}
          <div
            style={{
              position: 'absolute',
              inset: 0,
              background: 'rgba(0,0,0,0.5)',
            }}
          />

          {/* Modal container */}
          <div
            style={{
              position: 'absolute',
              inset: 0,
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
            }}
          >
            {/* Modal */}
            <div
              style={{
                width: 780,
                height: 620,
                background: '#fff',
                border: '2px solid #000',
                boxShadow: '8px 8px 0px 0px rgba(0,0,0,1)',
                display: 'flex',
                flexDirection: 'column',
                overflow: 'hidden',
              }}
            >
              {/* ============================================================
                  MODAL HEADER – Stepper
                  ============================================================ */}
              <div
                style={{
                  padding: '24px 36px',
                  borderBottom: '2px solid #000',
                  background: '#fafafa',
                }}
              >
                <div
                  style={{
                    display: 'flex',
                    alignItems: 'center',
                    justifyContent: 'center',
                    gap: 0,
                  }}
                >
                  {STEP_LABELS.map((label, i) => {
                    const stepNum = i + 1;
                    const isActive = stepNum === currentStep;
                    const isCompleted = stepNum < currentStep;
                    const isUpcoming = stepNum > currentStep;

                    return (
                      <React.Fragment key={i}>
                        {/* Step circle + label */}
                        <div
                          style={{
                            display: 'flex',
                            flexDirection: 'column',
                            alignItems: 'center',
                            gap: 6,
                          }}
                        >
                          <div
                            style={{
                              width: 32,
                              height: 32,
                              borderRadius: '50%',
                              border: '2px solid #000',
                              background: isCompleted || isActive ? '#000' : '#fff',
                              color: isCompleted || isActive ? '#fff' : '#000',
                              display: 'flex',
                              alignItems: 'center',
                              justifyContent: 'center',
                              fontSize: 13,
                              fontWeight: 800,
                              fontFamily: FONT_FAMILY,
                            }}
                          >
                            {isCompleted ? '\u2713' : stepNum}
                          </div>
                          <span
                            style={{
                              fontSize: 11,
                              fontWeight: 700,
                              fontFamily: FONT_FAMILY,
                              textTransform: 'uppercase',
                              letterSpacing: 1,
                              color: isUpcoming ? '#999' : '#000',
                            }}
                          >
                            {label}
                          </span>
                        </div>

                        {/* Connector line */}
                        {i < STEP_LABELS.length - 1 && (
                          <div
                            style={{
                              width: 48,
                              height: 2,
                              background: isCompleted ? '#000' : '#ddd',
                              marginBottom: 20,
                              marginLeft: 8,
                              marginRight: 8,
                            }}
                          />
                        )}
                      </React.Fragment>
                    );
                  })}
                </div>
              </div>

              {/* ============================================================
                  MODAL BODY – Step content
                  ============================================================ */}
              <div
                style={{
                  flex: 1,
                  padding: '28px 36px',
                  opacity: stepFadeIn,
                  overflow: 'hidden',
                }}
              >
                {/* --------------------------------------------------------
                    STEP 1: Customize Output
                    -------------------------------------------------------- */}
                {currentStep === 1 && (
                  <div>
                    <div
                      style={{
                        fontSize: 14,
                        fontWeight: 800,
                        fontFamily: FONT_FAMILY,
                        textTransform: 'uppercase',
                        letterSpacing: 1,
                        marginBottom: 20,
                        color: '#000',
                      }}
                    >
                      SELECT FOCUS AREAS
                    </div>
                    <div
                      style={{
                        fontSize: 13,
                        fontFamily: FONT_FAMILY,
                        color: '#666',
                        marginBottom: 16,
                      }}
                    >
                      Choose what to include in your analysis
                    </div>

                    {/* Focus area chips */}
                    <div
                      style={{
                        display: 'flex',
                        flexWrap: 'wrap',
                        gap: 10,
                      }}
                    >
                      {FOCUS_AREAS.map((area, i) => {
                        const isSelected = selectedFocusAreas.has(i);
                        return (
                          <div
                            key={i}
                            style={{
                              padding: '8px 14px',
                              fontSize: 11,
                              fontWeight: 700,
                              fontFamily: FONT_FAMILY,
                              textTransform: 'uppercase',
                              border: '2px solid #000',
                              background: isSelected ? APP_COLORS.primaryBlue : '#fff',
                              color: isSelected ? '#fff' : '#000',
                              boxShadow: isSelected
                                ? '2px 2px 0px 0px rgba(0,0,0,1)'
                                : 'none',
                            }}
                          >
                            {area}
                          </div>
                        );
                      })}
                    </div>
                  </div>
                )}

                {/* --------------------------------------------------------
                    STEP 2: Health Analysis
                    -------------------------------------------------------- */}
                {currentStep === 2 && (
                  <div>
                    <div
                      style={{
                        fontSize: 14,
                        fontWeight: 800,
                        fontFamily: FONT_FAMILY,
                        textTransform: 'uppercase',
                        letterSpacing: 1,
                        marginBottom: 12,
                        color: '#000',
                      }}
                    >
                      INCLUDE HEALTH ANALYSIS?
                    </div>
                    <div
                      style={{
                        fontSize: 13,
                        fontFamily: FONT_FAMILY,
                        color: '#666',
                        marginBottom: 24,
                      }}
                    >
                      Add a comprehensive financial health score to your brief
                    </div>

                    <div
                      style={{
                        display: 'flex',
                        gap: 20,
                        justifyContent: 'center',
                      }}
                    >
                      {/* Yes button */}
                      <div
                        style={{
                          flex: 1,
                          padding: 20,
                          textAlign: 'center' as const,
                          fontSize: 14,
                          fontWeight: 800,
                          fontFamily: FONT_FAMILY,
                          textTransform: 'uppercase',
                          border: '2px solid #000',
                          background:
                            healthSelected && currentStep === 2
                              ? '#4ade80'
                              : '#4ade80',
                          color: '#000',
                          boxShadow:
                            healthSelected && currentStep === 2
                              ? 'none'
                              : '4px 4px 0px 0px rgba(0,0,0,1)',
                          transform:
                            healthSelected && currentStep === 2
                              ? 'translate(2px, 2px)'
                              : 'translate(0,0)',
                        }}
                      >
                        Yes, Include It
                      </div>

                      {/* No button */}
                      <div
                        style={{
                          flex: 1,
                          padding: 20,
                          textAlign: 'center' as const,
                          fontSize: 14,
                          fontWeight: 800,
                          fontFamily: FONT_FAMILY,
                          textTransform: 'uppercase',
                          border: '2px solid #000',
                          background: '#f87171',
                          color: '#000',
                          boxShadow: '4px 4px 0px 0px rgba(0,0,0,1)',
                          opacity: healthSelected && currentStep === 2 ? 0.4 : 1,
                        }}
                      >
                        No, Skip It
                      </div>
                    </div>
                  </div>
                )}

                {/* --------------------------------------------------------
                    STEP 3: Select Persona
                    -------------------------------------------------------- */}
                {currentStep === 3 && (
                  <div>
                    <div
                      style={{
                        fontSize: 14,
                        fontWeight: 800,
                        fontFamily: FONT_FAMILY,
                        textTransform: 'uppercase',
                        letterSpacing: 1,
                        marginBottom: 12,
                        color: '#000',
                      }}
                    >
                      CHOOSE INVESTOR PERSONA
                    </div>
                    <div
                      style={{
                        fontSize: 13,
                        fontFamily: FONT_FAMILY,
                        color: '#666',
                        marginBottom: 16,
                      }}
                    >
                      Your brief will be written from this investor's perspective
                    </div>

                    {/* Persona grid — 4 columns, 2 rows */}
                    <div
                      style={{
                        display: 'grid',
                        gridTemplateColumns: '1fr 1fr 1fr 1fr',
                        gap: 12,
                      }}
                    >
                      {PERSONAS.map((persona, i) => {
                        const isBuffett = i === 0;
                        const isSelected = isBuffett && buffettSelected;

                        return (
                          <div
                            key={i}
                            style={{
                              border: '2px solid #000',
                              padding: 12,
                              display: 'flex',
                              flexDirection: 'column',
                              alignItems: 'center',
                              background: isSelected ? '#000' : '#fff',
                              color: isSelected ? '#fff' : '#000',
                              boxShadow: isSelected
                                ? '4px 4px 0px 0px rgba(0,0,0,1)'
                                : 'none',
                            }}
                          >
                            <Img
                              src={staticFile(persona.img)}
                              style={{
                                width: 48,
                                height: 48,
                                borderRadius: '50%',
                                objectFit: 'cover',
                              }}
                            />
                            <div
                              style={{
                                fontSize: 12,
                                fontWeight: 700,
                                fontFamily: FONT_FAMILY,
                                marginTop: 8,
                                textAlign: 'center' as const,
                              }}
                            >
                              {persona.name}
                            </div>
                            <div
                              style={{
                                fontSize: 10,
                                fontFamily: FONT_FAMILY,
                                color: isSelected ? 'rgba(255,255,255,0.7)' : '#666',
                                textAlign: 'center' as const,
                              }}
                            >
                              {persona.style}
                            </div>
                          </div>
                        );
                      })}
                    </div>
                  </div>
                )}

                {/* --------------------------------------------------------
                    STEP 4: Ready to Generate
                    -------------------------------------------------------- */}
                {currentStep === 4 && (
                  <div>
                    <div
                      style={{
                        fontSize: 14,
                        fontWeight: 800,
                        fontFamily: FONT_FAMILY,
                        textTransform: 'uppercase',
                        letterSpacing: 1,
                        marginBottom: 20,
                        color: '#000',
                      }}
                    >
                      REVIEW & GENERATE
                    </div>

                    {/* Summary list */}
                    <div style={{ marginBottom: 24 }}>
                      {[
                        { label: 'Filing', value: '10-K \u00B7 2024-11-01' },
                        {
                          label: 'Focus',
                          value:
                            'Financial Performance, Strategy & Execution, Liquidity & Balance Sheet',
                        },
                        { label: 'Health Analysis', value: 'Included' },
                        { label: 'Persona', value: 'Warren Buffett' },
                      ].map((item, i) => (
                        <div
                          key={i}
                          style={{
                            display: 'flex',
                            flexDirection: 'row',
                            gap: 8,
                            marginBottom: 12,
                            fontSize: 13,
                            fontFamily: FONT_FAMILY,
                          }}
                        >
                          <span style={{ fontWeight: 700, color: '#000' }}>
                            {item.label}:
                          </span>
                          <span style={{ fontWeight: 400, color: '#333' }}>
                            {item.value}
                          </span>
                        </div>
                      ))}
                    </div>

                    {/* Complete button */}
                    <div
                      style={{
                        background: '#000',
                        color: '#fff',
                        padding: '14px 48px',
                        textAlign: 'center' as const,
                        fontSize: 14,
                        fontWeight: 800,
                        fontFamily: FONT_FAMILY,
                        textTransform: 'uppercase',
                        letterSpacing: 2,
                        border: '2px solid #000',
                        width: '100%',
                        boxSizing: 'border-box' as const,
                        boxShadow: completeClicked
                          ? 'none'
                          : '4px 4px 0px 0px rgba(128,128,128,1)',
                        transform: completeClicked
                          ? 'translate(4px, 4px)'
                          : 'translate(0,0)',
                      }}
                    >
                      Complete
                    </div>
                  </div>
                )}
              </div>
            </div>
          </div>

          {/* Cursor INSIDE BrowserFrame — transforms with 3D perspective */}
          <Cursor
            path={cursorPath}
            clickFrames={clickFrames}
            visible={frame > 5 && frame < 285}
          />
        </div>
      </BrowserFrame>
    </AbsoluteFill>
  );
};
