import React from 'react';
import { AbsoluteFill, Sequence } from 'remotion';
import { WTLandingScene } from './scenes/WTLandingScene';
import { WTSearchScene } from './scenes/WTSearchScene';
import { WTCompanyScene } from './scenes/WTCompanyScene';
import { WTWizardScene } from './scenes/WTWizardScene';
import { WTGeneratingScene } from './scenes/WTGeneratingScene';
import { WTResultsScene } from './scenes/WTResultsScene';
import { FPS, WT_DURATIONS } from './constants';

// Scene definitions with durations
const scenes = [
  { Component: WTLandingScene, duration: WT_DURATIONS.landing * FPS },
  { Component: WTSearchScene, duration: WT_DURATIONS.search * FPS },
  { Component: WTCompanyScene, duration: WT_DURATIONS.companyPage * FPS },
  { Component: WTWizardScene, duration: WT_DURATIONS.wizard * FPS },
  { Component: WTGeneratingScene, duration: WT_DURATIONS.generating * FPS },
  { Component: WTResultsScene, duration: WT_DURATIONS.results * FPS },
];

// Pre-compute offsets
const sceneOffsets = scenes.reduce<number[]>((acc, _, i) => {
  if (i === 0) return [0];
  return [...acc, acc[i - 1] + scenes[i - 1].duration];
}, []);

export const WT_TOTAL_FRAMES = scenes.reduce((sum, s) => sum + s.duration, 0);

export const WalkthroughVideo: React.FC = () => {
  return (
    <AbsoluteFill style={{ backgroundColor: '#050505' }}>
      {scenes.map(({ Component, duration }, i) => (
        <Sequence
          key={i}
          from={sceneOffsets[i]}
          durationInFrames={duration}
          premountFor={FPS}
        >
          <Component />
        </Sequence>
      ))}
    </AbsoluteFill>
  );
};
