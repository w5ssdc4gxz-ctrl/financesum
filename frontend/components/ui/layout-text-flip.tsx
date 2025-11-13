"use client";

import React, { useState, useEffect, useMemo } from "react";
import { motion, AnimatePresence } from "motion/react";

import { cn } from "@/lib/utils";

interface LayoutTextFlipProps {
  text?: string;
  words?: string[];
  duration?: number;
  className?: string;
  textClassName?: string;
  pillClassName?: string;
}

export const LayoutTextFlip = ({
  text = "Build Amazing",
  words = ["Landing Pages", "Component Blocks", "Page Sections", "3D Shaders"],
  duration = 3000,
  className,
  textClassName,
  pillClassName,
}: LayoutTextFlipProps) => {
  const sanitizedWords = useMemo(() => (words?.length ? words : [""]), [words]);
  const [currentIndex, setCurrentIndex] = useState(0);

  useEffect(() => {
    if (sanitizedWords.length <= 1) return;
    const interval = setInterval(() => {
      setCurrentIndex((prevIndex) => (prevIndex + 1) % sanitizedWords.length);
    }, duration);

    return () => clearInterval(interval);
  }, [duration, sanitizedWords.length]);

  return (
    <div
      className={cn(
        "flex flex-wrap items-center justify-center gap-3 text-center sm:justify-start sm:text-left",
        className,
      )}
    >
      {text && (
        <motion.span
          layoutId="layout-text-flip-label"
          className={cn(
            "text-3xl font-black tracking-tight drop-shadow-lg md:text-5xl",
            textClassName,
          )}
        >
          {text}
        </motion.span>
      )}

      <motion.span
        layout
        className={cn(
          "relative w-fit overflow-hidden rounded-full border border-transparent bg-white px-6 py-3 font-sans text-3xl font-black tracking-tight text-black shadow-[0_25px_55px_rgba(5,0,21,0.45)] drop-shadow-lg md:text-5xl dark:bg-neutral-900 dark:text-white dark:shadow-sm dark:ring-1 dark:shadow-white/10 dark:ring-white/10",
          pillClassName,
        )}
      >
        <AnimatePresence mode="popLayout">
          <motion.span
            key={currentIndex}
            initial={{ y: -40, filter: "blur(10px)", opacity: 0 }}
            animate={{
              y: 0,
              filter: "blur(0px)",
              opacity: 1,
            }}
            exit={{ y: 50, filter: "blur(10px)", opacity: 0 }}
            transition={{
              duration: 0.5,
            }}
            className="inline-block whitespace-nowrap"
          >
            {sanitizedWords[currentIndex]}
          </motion.span>
        </AnimatePresence>
      </motion.span>
    </div>
  );
};

export default LayoutTextFlip;
