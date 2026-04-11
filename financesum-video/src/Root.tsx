import React from 'react';
import { Composition } from 'remotion';
import { FinanceSumVideo, TOTAL_FRAMES } from './Composition';
import { WalkthroughVideo, WT_TOTAL_FRAMES } from './walkthrough/WalkthroughComposition';
import { FPS } from './constants';

export const RemotionRoot: React.FC = () => {
  return (
    <>
      <Composition
        id="FinanceSumPromo"
        component={FinanceSumVideo}
        durationInFrames={TOTAL_FRAMES}
        fps={FPS}
        width={1920}
        height={1080}
      />
      <Composition
        id="FinanceSumWalkthrough"
        component={WalkthroughVideo}
        durationInFrames={WT_TOTAL_FRAMES}
        fps={FPS}
        width={1920}
        height={1080}
      />
    </>
  );
};
