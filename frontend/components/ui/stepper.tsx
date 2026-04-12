'use client';

import React, { useState, Children, useRef, useEffect, useLayoutEffect } from 'react';
import { gsap } from 'gsap';
import { cn } from '@/lib/utils';

interface StepperProps extends React.HTMLAttributes<HTMLDivElement> {
    initialStep?: number;
    onStepChange?: (step: number) => void;
    onFinalStepCompleted?: () => void;
    stepCircleContainerClassName?: string;
    stepContainerClassName?: string;
    contentClassName?: string;
    footerClassName?: string;
    backButtonProps?: React.ButtonHTMLAttributes<HTMLButtonElement>;
    nextButtonProps?: React.ButtonHTMLAttributes<HTMLButtonElement>;
    backButtonText?: string;
    nextButtonText?: string;
    disableStepIndicators?: boolean;
    renderStepIndicator?: (props: {
        step: number;
        currentStep: number;
        onStepClick: (step: number) => void;
    }) => React.ReactNode;
    children: React.ReactNode;
}

export default function Stepper({
    children,
    initialStep = 1,
    onStepChange = () => { },
    onFinalStepCompleted = () => { },
    stepCircleContainerClassName = '',
    stepContainerClassName = '',
    contentClassName = '',
    footerClassName = '',
    backButtonProps = {},
    nextButtonProps = {},
    backButtonText = 'Back',
    nextButtonText = 'Continue',
    disableStepIndicators = false,
    renderStepIndicator,
    className,
    ...rest
}: StepperProps) {
    const [currentStep, setCurrentStep] = useState(initialStep);
    const [direction, setDirection] = useState(0);
    const stepsArray = Children.toArray(children);
    const totalSteps = stepsArray.length;
    const isCompleted = currentStep > totalSteps;
    const isLastStep = currentStep === totalSteps;

    const updateStep = (newStep: number) => {
        setDirection(newStep > currentStep ? 1 : -1);
        setCurrentStep(newStep);
        if (newStep > totalSteps) {
            onFinalStepCompleted();
        } else {
            onStepChange(newStep);
        }
    };

    const handleBack = () => {
        if (currentStep > 1) {
            updateStep(currentStep - 1);
        }
    };

    const handleNext = () => {
        if (!isLastStep) {
            updateStep(currentStep + 1);
        }
    };

    const handleComplete = () => {
        updateStep(totalSteps + 1);
    };

    return (
        <div className={cn("flex flex-col min-h-full w-full", className)} {...rest}>
            <div className={cn("w-full mx-auto border-2 border-black dark:border-white shadow-[4px_4px_0px_0px_rgba(0,0,0,1)] dark:shadow-[4px_4px_0px_0px_rgba(255,255,255,1)] bg-white dark:bg-zinc-900", stepCircleContainerClassName)}>
                <div className={cn("flex w-full items-center p-8", stepContainerClassName)}>
                    {stepsArray.map((_, index) => {
                        const stepNumber = index + 1;
                        const isNotLastStep = index < totalSteps - 1;
                        return (
                            <React.Fragment key={stepNumber}>
                                {renderStepIndicator ? (
                                    renderStepIndicator({
                                        step: stepNumber,
                                        currentStep,
                                        onStepClick: (clicked) => updateStep(clicked)
                                    })
                                ) : (
                                    <StepIndicator
                                        step={stepNumber}
                                        disableStepIndicators={disableStepIndicators}
                                        currentStep={currentStep}
                                        onClickStep={(clicked) => updateStep(clicked)}
                                    />
                                )}
                                {isNotLastStep && <StepConnector isComplete={currentStep > stepNumber} />}
                            </React.Fragment>
                        );
                    })}
                </div>

                <StepContentWrapper
                    isCompleted={isCompleted}
                    currentStep={currentStep}
                    direction={direction}
                    className={cn("relative overflow-hidden px-8", contentClassName)}
                >
                    {stepsArray[currentStep - 1]}
                </StepContentWrapper>

                {!isCompleted && (
                    <div className={cn("px-8 pb-8", footerClassName)}>
                        <div className={cn("mt-10 flex", currentStep !== 1 ? 'justify-between' : 'justify-end')}>
                            {currentStep !== 1 && (
                                <button
                                    onClick={handleBack}
                                    className={cn(
                                        "px-6 py-2 font-bold uppercase border-2 border-black dark:border-white hover:bg-gray-100 dark:hover:bg-zinc-800 transition-all",
                                        currentStep === 1 ? 'pointer-events-none opacity-50' : ''
                                    )}
                                    {...backButtonProps}
                                >
                                    {backButtonText}
                                </button>
                            )}
                            <button
                                onClick={isLastStep ? handleComplete : handleNext}
                                className="px-6 py-2 font-bold uppercase bg-black text-white dark:bg-white dark:text-black border-2 border-black dark:border-white shadow-[2px_2px_0px_0px_rgba(128,128,128,1)] hover:translate-y-[-1px] hover:shadow-[3px_3px_0px_0px_rgba(128,128,128,1)] active:translate-y-[0px] active:shadow-[1px_1px_0px_0px_rgba(128,128,128,1)] transition-all"
                                {...nextButtonProps}
                            >
                                {isLastStep ? 'Complete' : nextButtonText}
                            </button>
                        </div>
                    </div>
                )}
            </div>
        </div>
    );
}

interface StepContentWrapperProps {
    isCompleted: boolean;
    currentStep: number;
    direction: number;
    children: React.ReactNode;
    className?: string;
}

function StepContentWrapper({ isCompleted, currentStep, direction, children, className }: StepContentWrapperProps) {
    const containerRef = useRef<HTMLDivElement>(null);
    const contentRef = useRef<HTMLDivElement>(null);
    const [height, setHeight] = useState<number | 'auto'>('auto');

    // Animate height
    useEffect(() => {
        if (!containerRef.current) return;

        const targetHeight = isCompleted ? 0 : (contentRef.current?.offsetHeight || 0);

        gsap.to(containerRef.current, {
            height: targetHeight,
            duration: 0.4,
            ease: "power2.out"
        });
    }, [currentStep, isCompleted, children]);

    return (
        <div
            ref={containerRef}
            className={className}
            style={{ height: 'auto' }} // Initial state
        >
            <div ref={contentRef} className="w-full p-2">
                <SlideTransition key={currentStep} direction={direction}>
                    {children}
                </SlideTransition>
            </div>
        </div>
    );
}

interface SlideTransitionProps {
    children: React.ReactNode;
    direction: number;
}

function SlideTransition({ children, direction }: SlideTransitionProps) {
    const elRef = useRef<HTMLDivElement>(null);

    useLayoutEffect(() => {
        if (!elRef.current) return;

        // Initial state for entering element
        gsap.fromTo(elRef.current,
            {
                x: direction > 0 ? '100%' : '-100%',
                opacity: 0
            },
            {
                x: '0%',
                opacity: 1,
                duration: 0.5,
                ease: "power3.out"
            }
        );

        return () => {
            // Cleanup if needed, though GSAP handles overwrites well
            if (elRef.current) gsap.killTweensOf(elRef.current);
        };
    }, []); // Empty dependency array to run only on mount (key change)

    return (
        <div ref={elRef} className="w-full">
            {children}
        </div>
    );
}

export function Step({ children }: { children: React.ReactNode }) {
    return <div className="w-full">{children}</div>;
}

interface StepIndicatorProps {
    step: number;
    currentStep: number;
    onClickStep: (step: number) => void;
    disableStepIndicators?: boolean;
}

function StepIndicator({ step, currentStep, onClickStep, disableStepIndicators }: StepIndicatorProps) {
    const status = currentStep === step ? 'active' : currentStep < step ? 'inactive' : 'complete';
    const ref = useRef<HTMLDivElement>(null);

    useEffect(() => {
        if (!ref.current) return;

        const colors = {
            inactive: { bg: '#fff', color: '#000', border: '#000' },
            active: { bg: '#000', color: '#fff', border: '#000' },
            complete: { bg: '#22c55e', color: '#000', border: '#000' }
        };

        const target = colors[status];

        gsap.to(ref.current, {
            backgroundColor: target.bg,
            color: target.color,
            borderColor: target.border,
            duration: 0.3,
            ease: "power2.out"
        });

    }, [status]);

    const handleClick = () => {
        if (step !== currentStep && !disableStepIndicators) onClickStep(step);
    };

    return (
        <div onClick={handleClick} className="relative cursor-pointer outline-none">
            <div
                ref={ref}
                className="flex h-8 w-8 items-center justify-center border-2 font-bold transition-colors"
                style={{ backgroundColor: '#fff', color: '#000', borderColor: '#000' }}
            >
                {status === 'complete' ? (
                    <CheckIcon className="h-4 w-4" />
                ) : (
                    <span className="text-sm">{step}</span>
                )}
            </div>
        </div>
    );
}

function StepConnector({ isComplete }: { isComplete: boolean }) {
    const ref = useRef<HTMLDivElement>(null);

    useEffect(() => {
        if (!ref.current) return;

        gsap.to(ref.current, {
            width: isComplete ? '100%' : '0%',
            backgroundColor: isComplete ? '#000' : 'transparent',
            duration: 0.4,
            ease: "power2.inOut"
        });
    }, [isComplete]);

    return (
        <div className="relative mx-2 h-0.5 flex-1 overflow-hidden bg-gray-200 dark:bg-gray-700">
            <div
                ref={ref}
                className="absolute left-0 top-0 h-full"
                style={{ width: '0%', backgroundColor: 'transparent' }}
            />
        </div>
    );
}

function CheckIcon(props: React.SVGProps<SVGSVGElement>) {
    const pathRef = useRef<SVGPathElement>(null);

    useLayoutEffect(() => {
        if (!pathRef.current) return;
        const length = pathRef.current.getTotalLength();

        gsap.set(pathRef.current, { strokeDasharray: length, strokeDashoffset: length });
        gsap.to(pathRef.current, {
            strokeDashoffset: 0,
            duration: 0.3,
            ease: "power2.out",
            delay: 0.1
        });
    }, []);

    return (
        <svg {...props} fill="none" stroke="currentColor" strokeWidth={3} viewBox="0 0 24 24">
            <path
                ref={pathRef}
                strokeLinecap="round"
                strokeLinejoin="round"
                d="M5 13l4 4L19 7"
            />
        </svg>
    );
}
