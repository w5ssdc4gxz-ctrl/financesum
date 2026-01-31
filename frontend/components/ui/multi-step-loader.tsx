"use client";

import {
  AnimatePresence,
  animate,
  motion,
  useMotionValue,
  useMotionValueEvent,
  useTransform,
} from "framer-motion";
import { createPortal } from "react-dom";
import { useEffect, useMemo, useRef, useState } from "react";

export type SummaryProgressPayload = {
  status?: string | null;
  percent?: number | null;
  percent_exact?: number | null;
  eta_seconds?: number | null;
};

const formatEta = (etaSeconds: number | null | undefined) => {
  if (etaSeconds === null || etaSeconds === undefined) return "Taking longer than usual…";
  const total = Math.max(0, Math.floor(etaSeconds));
  const minutes = Math.floor(total / 60);
  const seconds = total % 60;
  if (minutes <= 0) return `${seconds}s remaining`;
  return `${minutes}m ${String(seconds).padStart(2, "0")}s remaining`;
};

export const MultiStepLoader = ({
  loading,
  progress,
  title = "Generating AI Brief",
}: {
  loading?: boolean;
  progress?: SummaryProgressPayload | null;
  title?: string;
}) => {
  const [mounted, setMounted] = useState(false);
  const progressValue = useMotionValue(0);
  const progressWidth = useTransform(
    progressValue,
    (value) => `${Math.max(0, Math.min(100, value)).toFixed(2)}%`,
  );
  const [displayPercent, setDisplayPercent] = useState(0);
  const displayPercentRef = useRef(0);

  useEffect(() => {
    setMounted(true);
    return () => setMounted(false);
  }, []);

  useMotionValueEvent(progressValue, "change", (value) => {
    const next = Math.max(displayPercentRef.current, Math.max(0, Math.min(100, Math.floor(value))));
    if (next === displayPercentRef.current) return;
    displayPercentRef.current = next;
    setDisplayPercent(next);
  });

  const targetPercent = useMemo(() => {
    const exact =
      typeof progress?.percent_exact === "number" && Number.isFinite(progress.percent_exact)
        ? progress.percent_exact
        : null;
    const fallback =
      typeof progress?.percent === "number" && Number.isFinite(progress.percent) ? progress.percent : 0;
    const value = exact ?? fallback;
    return Math.max(0, Math.min(100, value));
  }, [progress?.percent, progress?.percent_exact]);

  useEffect(() => {
    if (!loading) {
      progressValue.set(0);
      displayPercentRef.current = 0;
      setDisplayPercent(0);
      return;
    }
    const current = progressValue.get();
    const target = targetPercent;
    const delta = Math.max(0, target - current);
    const duration = target >= 100 ? 0.25 : Math.min(2.6, 1.1 + delta * 0.04);
    const controls = animate(progressValue, target, {
      duration,
      ease: [0.2, 0.8, 0.2, 1],
    });
    return () => controls.stop();
  }, [loading, targetPercent, progressValue]);

  const status = (progress?.status || "Initializing…").toString();
  const etaLabel = formatEta(progress?.eta_seconds ?? null);

  const overlay = (
    <AnimatePresence mode="wait">
      {loading && (
        <motion.div
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          exit={{ opacity: 0 }}
          className="fixed inset-0 flex items-center justify-center bg-white/80 dark:bg-black/80 backdrop-blur-xl"
          style={{ zIndex: 100000 }}
        >
          <motion.div
            initial={{ opacity: 0, y: 12, scale: 0.98 }}
            animate={{ opacity: 1, y: 0, scale: 1 }}
            exit={{ opacity: 0, y: 12, scale: 0.98 }}
            transition={{ duration: 0.2 }}
            className="w-full max-w-md sm:max-w-xl mx-4 bg-white dark:bg-zinc-900 border-2 border-black dark:border-white shadow-[8px_8px_0px_0px_rgba(0,0,0,1)] dark:shadow-[8px_8px_0px_0px_rgba(255,255,255,1)] p-4 sm:p-6 max-h-[85vh] overflow-auto"
          >
            <div className="flex flex-col sm:flex-row sm:items-start sm:justify-between gap-4">
              <div>
                <div className="text-xs font-bold uppercase tracking-widest text-gray-500 dark:text-gray-400">
                  FinanceSum
                </div>
                <h2 className="mt-1 text-xl sm:text-2xl font-black uppercase leading-tight break-words">
                  {title}
                </h2>
              </div>
              <div className="sm:text-right">
                <div className="text-3xl sm:text-4xl font-black tabular-nums leading-none">
                  {displayPercent}%
                </div>
                <div className="text-xs font-mono text-gray-500 dark:text-gray-400">{etaLabel}</div>
              </div>
            </div>

            <div className="mt-5">
              <div className="flex items-center justify-between gap-3 text-xs font-mono text-gray-600 dark:text-gray-300">
                <span className="whitespace-normal break-words">{status}</span>
              </div>

              <div className="mt-3 border-2 border-black dark:border-white bg-gray-100 dark:bg-black h-3 overflow-hidden relative">
                <motion.div
                  className="h-full bg-black dark:bg-white"
                  initial={{ width: 0 }}
                  style={{ width: progressWidth }}
                  transition={{ type: "tween", duration: 0.2, ease: "easeOut" }}
                />
                <motion.div
                  aria-hidden
                  className="absolute top-0 h-full w-1/3 bg-gradient-to-r from-transparent via-white/40 to-transparent"
                  animate={{ x: ["-50%", "200%"] }}
                  transition={{ duration: 1.2, ease: "linear", repeat: Infinity }}
                  style={{ mixBlendMode: "overlay" }}
                />
              </div>
            </div>
          </motion.div>
        </motion.div>
      )}
    </AnimatePresence>
  );

  if (!mounted) return null;
  return createPortal(overlay, document.body);
};
