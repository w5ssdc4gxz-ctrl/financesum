import React, { useRef, useState, useEffect } from 'react';
import { cn } from '@/lib/utils';

interface BrutalSliderProps {
    value: number;
    min: number;
    max: number;
    step?: number;
    onChange: (value: number) => void;
    className?: string;
    label?: string;
}

export function BrutalSlider({
    value,
    min,
    max,
    step = 1,
    onChange,
    className,
    label,
}: BrutalSliderProps) {
    const trackRef = useRef<HTMLDivElement>(null);
    const [isDragging, setIsDragging] = useState(false);

    const percentage = Math.min(100, Math.max(0, ((value - min) / (max - min)) * 100));

    const handleInteraction = (clientX: number) => {
        if (!trackRef.current) return;
        const rect = trackRef.current.getBoundingClientRect();
        const x = Math.max(0, Math.min(clientX - rect.left, rect.width));
        const percent = x / rect.width;
        const rawValue = min + percent * (max - min);
        const steppedValue = Math.round(rawValue / step) * step;
        const clampedValue = Math.min(max, Math.max(min, steppedValue));
        onChange(clampedValue);
    };

    const handleMouseDown = (e: React.MouseEvent) => {
        setIsDragging(true);
        handleInteraction(e.clientX);
    };

    const handleTouchStart = (e: React.TouchEvent) => {
        setIsDragging(true);
        handleInteraction(e.touches[0].clientX);
    };

    useEffect(() => {
        const handleMouseMove = (e: MouseEvent) => {
            if (isDragging) {
                handleInteraction(e.clientX);
            }
        };

        const handleTouchMove = (e: TouchEvent) => {
            if (isDragging) {
                handleInteraction(e.touches[0].clientX);
            }
        };

        const handleMouseUp = () => {
            setIsDragging(false);
        };

        if (isDragging) {
            window.addEventListener('mousemove', handleMouseMove);
            window.addEventListener('touchmove', handleTouchMove);
            window.addEventListener('mouseup', handleMouseUp);
            window.addEventListener('touchend', handleMouseUp);
        }

        return () => {
            window.removeEventListener('mousemove', handleMouseMove);
            window.removeEventListener('touchmove', handleTouchMove);
            window.removeEventListener('mouseup', handleMouseUp);
            window.removeEventListener('touchend', handleMouseUp);
        };
    }, [isDragging, min, max, step, onChange]);

    return (
        <div className={cn("w-full select-none touch-none px-3", className)}>
            {label && (
                <div className="flex justify-between items-center mb-2">
                    <label className="text-xs font-bold uppercase">{label}</label>
                    <span className="font-mono text-xs font-bold bg-black text-white dark:bg-white dark:text-black px-2 py-0.5">
                        {value} words
                    </span>
                </div>
            )}
            <div
                ref={trackRef}
                className="relative h-8 cursor-pointer flex items-center"
                onMouseDown={handleMouseDown}
                onTouchStart={handleTouchStart}
            >
                {/* Track Line */}
                <div className="absolute w-full h-1 bg-gray-200 dark:bg-gray-800 border border-black dark:border-white" />

                {/* Active Track Line */}
                <div
                    className="absolute h-1 bg-black dark:bg-white border-y border-l border-black dark:border-white"
                    style={{ width: `${percentage}%` }}
                />

                {/* Thumb */}
                <div
                    className={cn(
                        "absolute w-6 h-6 rounded-full bg-white dark:bg-black border-2 border-black dark:border-white shadow-[2px_2px_0px_0px_rgba(0,0,0,1)] transition-transform hover:scale-110",
                        isDragging && "scale-110 shadow-[4px_4px_0px_0px_rgba(0,0,0,1)]"
                    )}
                    style={{
                        left: `calc(${percentage}% - 12px)`,
                    }}
                />
            </div>
        </div>
    );
}
