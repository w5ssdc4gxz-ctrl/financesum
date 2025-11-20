import React from "react";
import { cn } from "@/lib/utils";

interface BrutalButtonProps extends React.ButtonHTMLAttributes<HTMLButtonElement> {
    children: React.ReactNode;
    className?: string;
    variant?: "primary" | "secondary" | "outline" | "unapologetic" | "outline-rounded" | "brutal-stacked";
}

export function BrutalButton({
    children,
    variant = 'primary',
    className,
    ...props
}: BrutalButtonProps) {
    const variants = {
        primary: "bg-black text-white dark:bg-white dark:text-black border-2 border-black dark:border-white shadow-[4px_4px_0px_0px_rgba(128,128,128,1)] hover:translate-y-[-2px] hover:translate-x-[-2px] hover:shadow-[6px_6px_0px_0px_rgba(128,128,128,1)] active:translate-y-[0px] active:translate-x-[0px] active:shadow-[2px_2px_0px_0px_rgba(128,128,128,1)]",
        secondary: "bg-white text-black dark:bg-black dark:text-white border-2 border-black dark:border-white shadow-[4px_4px_0px_0px_rgba(0,0,0,1)] dark:shadow-[4px_4px_0px_0px_rgba(255,255,255,1)] hover:translate-y-[-2px] hover:translate-x-[-2px] hover:shadow-[6px_6px_0px_0px_rgba(0,0,0,1)] dark:hover:shadow-[6px_6px_0px_0px_rgba(255,255,255,1)] active:translate-y-[0px] active:translate-x-[0px] active:shadow-[2px_2px_0px_0px_rgba(0,0,0,1)] dark:active:shadow-[2px_2px_0px_0px_rgba(255,255,255,1)]",
        outline: "bg-transparent text-black dark:text-white border-2 border-black dark:border-white hover:bg-black hover:text-white dark:hover:bg-white dark:hover:text-black",

        // New variants
        unapologetic: "px-8 py-2 border border-black bg-transparent text-black dark:text-white dark:border-white relative group transition duration-200",
        'outline-rounded': "px-4 py-2 rounded-xl border border-neutral-600 text-black bg-white hover:bg-gray-100 transition duration-200",
        'brutal-stacked': "px-8 py-0.5 border-2 border-black dark:border-white uppercase bg-white text-black transition duration-200 text-sm shadow-[1px_1px_rgba(0,0,0),2px_2px_rgba(0,0,0),3px_3px_rgba(0,0,0),4px_4px_rgba(0,0,0),5px_5px_0px_0px_rgba(0,0,0)] dark:shadow-[1px_1px_rgba(255,255,255),2px_2px_rgba(255,255,255),3px_3px_rgba(255,255,255),4px_4px_rgba(255,255,255),5px_5px_0px_0px_rgba(255,255,255)] hover:translate-y-[-1px] hover:translate-x-[-1px] active:translate-y-[1px] active:translate-x-[1px]"
    }

    if (variant === 'unapologetic') {
        return (
            <button
                className={cn(variants[variant], className)}
                {...props}
            >
                <div className="absolute -bottom-2 -right-2 bg-yellow-300 h-full w-full -z-10 group-hover:bottom-0 group-hover:right-0 transition-all duration-200 border border-black" />
                <span className="relative font-bold uppercase">{children}</span>
            </button>
        )
    }

    return (
        <button
            className={cn(
                "px-4 py-2 font-bold uppercase text-sm transition-all duration-200",
                variants[variant],
                className
            )}
            {...props}
        >
            {children}
        </button>
    )
}
