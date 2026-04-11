import React from 'react';
import { AbsoluteFill, Sequence } from 'remotion';
import { IntroScene } from './scenes/IntroScene';
import { ProblemScene } from './scenes/ProblemScene';
import { SolutionScene } from './scenes/SolutionScene';
import { FeaturesScene } from './scenes/FeaturesScene';
import { PersonasScene } from './scenes/PersonasScene';
import { PipelineScene } from './scenes/PipelineScene';
import { PricingScene } from './scenes/PricingScene';
import { CtaScene } from './scenes/CtaScene';
import { FPS, SCENE_DURATIONS } from './constants';

// Pre-compute frame durations and offsets
const scenes = [
  { Component: IntroScene, duration: SCENE_DURATIONS.intro * FPS },
  { Component: ProblemScene, duration: SCENE_DURATIONS.problem * FPS },
  { Component: SolutionScene, duration: SCENE_DURATIONS.solution * FPS },
  { Component: FeaturesScene, duration: SCENE_DURATIONS.features * FPS },
  { Component: PersonasScene, duration: SCENE_DURATIONS.personas * FPS },
  { Component: PipelineScene, duration: SCENE_DURATIONS.pipeline * FPS },
  { Component: PricingScene, duration: SCENE_DURATIONS.pricing * FPS },
  { Component: CtaScene, duration: SCENE_DURATIONS.cta * FPS },
];

const sceneOffsets = scenes.reduce<number[]>((acc, _, i) => {
  if (i === 0) return [0];
  return [...acc, acc[i - 1] + scenes[i - 1].duration];
}, []);

export const TOTAL_FRAMES = scenes.reduce((sum, s) => sum + s.duration, 0);

export const FinanceSumVideo: React.FC = () => {
  return (
    <AbsoluteFill style={{ backgroundColor: '#020617' }}>
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
