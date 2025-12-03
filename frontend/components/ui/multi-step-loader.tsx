"use client";
import { cn } from "@/lib/utils";
import { AnimatePresence, motion } from "framer-motion";
import { useState, useEffect } from "react";

const CheckIcon = ({ className }: { className?: string }) => {
    return (
        <svg
            xmlns="http://www.w3.org/2000/svg"
            fill="none"
            viewBox="0 0 24 24"
            strokeWidth={2.5}
            stroke="currentColor"
            className={cn("w-6 h-6", className)}
        >
            <path d="M5 13l4 4L19 7" />
        </svg>
    );
};

const CheckFilled = ({ className }: { className?: string }) => {
    return (
        <svg
            xmlns="http://www.w3.org/2000/svg"
            viewBox="0 0 24 24"
            fill="currentColor"
            className={cn("w-6 h-6", className)}
        >
            <path
                fillRule="evenodd"
                d="M2.25 12c0-5.385 4.365-9.75 9.75-9.75s9.75 4.365 9.75 9.75-4.365 9.75-9.75 9.75S2.25 17.385 2.25 12zm13.36-1.814a.75.75 0 10-1.22-.872l-3.236 4.53L9.53 12.22a.75.75 0 00-1.06 1.06l2.25 2.25a.75.75 0 001.14-.094l3.75-5.25z"
                clipRule="evenodd"
            />
        </svg>
    );
};

const LoaderIcon = ({ className }: { className?: string }) => {
    return (
        <svg
            xmlns="http://www.w3.org/2000/svg"
            fill="none"
            viewBox="0 0 24 24"
            strokeWidth={2.5}
            stroke="currentColor"
            className={cn("w-6 h-6 animate-spin", className)}
        >
            <path strokeLinecap="round" strokeLinejoin="round" d="M16.023 9.348h4.992v-.001M2.985 19.644v-4.992m0 0h4.992m-4.993 0l3.181 3.183a8.25 8.25 0 0013.803-3.7M4.031 9.865a8.25 8.25 0 0113.803-3.7l3.181 3.182m0-4.991v4.99" />
        </svg>
    );
};

type LoadingState = {
    text: string;
};

const LoaderCore = ({
    loadingStates,
    value = 0,
    percentage,
    statusText,
}: {
    loadingStates: LoadingState[];
    value?: number;
    percentage?: number | null;
    statusText?: string;
}) => {
    return (
        <div className="flex relative justify-start max-w-xl mx-auto flex-col mt-40">
            {loadingStates.map((loadingState, index) => {
                const distance = Math.abs(index - value);
                const opacity = Math.max(1 - distance * 0.2, 0);

                return (
                    <motion.div
                        key={index}
                        className={cn("text-left flex gap-2 mb-4")}
                        initial={{ opacity: 0, y: -(value * 40) }}
                        animate={{ opacity: opacity, y: -(value * 40) }}
                        transition={{ duration: 0.5 }}
                    >
                        <div className="w-6 h-6 flex items-center justify-center">
                            {index > value ? (
                                <div className="w-4 h-4 border-2 border-black dark:border-white" />
                            ) : index === value ? (
                                <LoaderIcon className="text-black dark:text-white" />
                            ) : (
                                <CheckFilled className={cn("text-black dark:text-white", value === index && "text-black dark:text-white opacity-100")} />
                            )}
                        </div>
                        <span
                            className={cn(
                                "text-black dark:text-white text-lg font-bold uppercase",
                                value === index && "text-black dark:text-white opacity-100"
                            )}
                        >
                            {index === value && statusText ? statusText : loadingState.text}
                        </span>
                    </motion.div>
                );
            })}
            
            {percentage !== null && percentage !== undefined && (
                <motion.div
                    initial={{ opacity: 0 }}
                    animate={{ opacity: 1 }}
                    className="mt-8 w-full max-w-md"
                >
                    <div className="flex justify-between text-sm font-bold mb-2">
                        <span>Progress</span>
                        <span>{percentage}%</span>
                    </div>
                    <div className="h-4 bg-gray-200 dark:bg-gray-700 border-2 border-black dark:border-white">
                        <motion.div
                            className="h-full bg-black dark:bg-white"
                            initial={{ width: 0 }}
                            animate={{ width: `${percentage}%` }}
                            transition={{ duration: 0.3 }}
                        />
                    </div>
                </motion.div>
            )}
        </div>
    );
};

export const MultiStepLoader = ({
    loadingStates,
    loading,
    duration = 2000,
    loop = true,
    stopOnLastStep,
    currentStep,
    percentage,
    statusText,
}: {
    loadingStates: LoadingState[];
    loading?: boolean;
    duration?: number;
    loop?: boolean;
    stopOnLastStep?: boolean;
    currentStep?: number;
    percentage?: number | null;
    statusText?: string;
}) => {
    const [internalState, setInternalState] = useState(0);

    // Use external state if provided, otherwise internal
    const currentState = currentStep !== undefined ? currentStep : internalState;

    useEffect(() => {
        if (!loading) {
            setInternalState(0);
            return;
        }

        // If controlled externally, skip internal timer
        if (currentStep !== undefined) return;

        // Add some randomness to the duration for a more "organic" feel
        const randomDuration = duration * (0.8 + Math.random() * 0.4);

        const timeout = setTimeout(() => {
            setInternalState((prevState) => {
                if (stopOnLastStep && prevState === loadingStates.length - 1) {
                    return prevState;
                }
                return loop
                    ? (prevState + 1) % loadingStates.length
                    : Math.min(prevState + 1, loadingStates.length - 1);
            });
        }, randomDuration);

        return () => clearTimeout(timeout);
    }, [internalState, loading, loop, loadingStates.length, duration, stopOnLastStep, currentStep]);

    return (
        <AnimatePresence mode="wait">
            {loading && (
                <motion.div
                    initial={{ opacity: 0 }}
                    animate={{ opacity: 1 }}
                    exit={{ opacity: 0 }}
                    className="w-full h-full fixed inset-0 z-[100] flex items-center justify-center bg-white/90 dark:bg-black/90 backdrop-blur-sm border-2 border-black dark:border-white"
                >
                    <div className="h-96 relative">
                        <LoaderCore 
                            value={currentState} 
                            loadingStates={loadingStates} 
                            percentage={percentage}
                            statusText={statusText}
                        />
                    </div>

                    <div className="bg-gradient-to-t inset-x-0 z-20 bottom-0 bg-white dark:bg-black h-full absolute [mask-image:radial-gradient(900px_at_center,transparent_30%,white)]" />
                </motion.div>
            )}
        </AnimatePresence>
    );
};
