'use client'

import React from 'react'

interface GradientButtonProps extends React.ButtonHTMLAttributes<HTMLButtonElement> {
    children: React.ReactNode
}

export function GradientButton({ children, className, ...props }: GradientButtonProps) {
    return (
        <div className={`gradient-btn-wrapper ${className || ''}`}>
            <button className="gradient-btn" {...props}>
                <div>
                    <span>{children}</span>
                </div>
            </button>
        </div>
    )
}
