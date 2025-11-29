import React, { useState } from 'react';

import Stepper, { Step } from '@/components/ui/stepper';
import { BrutalButton } from '@/components/ui/BrutalButton';
import { MultiStepLoader } from '@/components/ui/multi-step-loader';
import { Modal } from '@/components/ui/modal';
import { BrutalSlider } from '@/components/ui/brutal-slider';
import { cn } from '@/lib/utils';

// ... (Types and Constants remain the same)

// --- Types (Mirrored from page.tsx for self-containment) ---
export type SummaryMode = 'default' | 'custom';
export type SummaryTone = 'objective' | 'cautiously optimistic' | 'bullish' | 'bearish';
export type SummaryDetailLevel = 'snapshot' | 'balanced' | 'deep dive';
export type SummaryOutputStyle = 'narrative' | 'bullets' | 'mixed';
export type SummaryComplexity = 'simple' | 'intermediate' | 'expert';

export type HealthFramework =
    | 'value_investor_default'
    | 'quality_moat_focus'
    | 'financial_resilience'
    | 'growth_sustainability'
    | 'user_defined_mix';

export type HealthWeighting =
    | 'profitability_margins'
    | 'cash_flow_conversion'
    | 'balance_sheet_strength'
    | 'liquidity_near_term_risk'
    | 'execution_competitiveness';

export type HealthRiskTolerance = 'very_conservative' | 'moderately_conservative' | 'balanced' | 'moderately_lenient' | 'very_lenient';
export type HealthAnalysisDepth = 'headline_only' | 'key_financial_items' | 'full_footnote_review' | 'accounting_integrity' | 'forensic_deep_dive';
export type HealthDisplayStyle = 'score_only' | 'score_plus_grade' | 'score_plus_traffic_light' | 'score_plus_pillars' | 'score_with_narrative';

export type HealthRatingFormState = {
    enabled: boolean;
    framework: HealthFramework;
    weighting: HealthWeighting;
    riskTolerance: HealthRiskTolerance;
    analysisDepth: HealthAnalysisDepth;
    displayStyle: HealthDisplayStyle;
};

export type SummaryPreferenceFormState = {
    mode: SummaryMode;
    investorFocus: string;
    focusAreas: string[];
    tone: SummaryTone;
    detailLevel: SummaryDetailLevel;
    outputStyle: SummaryOutputStyle;
    targetLength: number;
    complexity: SummaryComplexity;
    healthRating: HealthRatingFormState;
    includeHealthScore: boolean;
    selectedPersona: string | null;
};

// --- Constants ---
const focusAreaOptions = [
    'Financial performance',
    'Risk factors',
    'Strategy & execution',
    'Capital allocation',
    'Liquidity & balance sheet',
    'Guidance & outlook',
];

const toneOptions = [
    { value: 'objective', label: 'Objective' },
    { value: 'cautiously optimistic', label: 'Cautiously Optimistic' },
    { value: 'bullish', label: 'Bullish' },
    { value: 'bearish', label: 'Bearish' },
];

const detailOptions = [
    { value: 'snapshot', label: 'Snapshot' },
    { value: 'balanced', label: 'Balanced' },
    { value: 'deep dive', label: 'Deep Dive' },
];

const outputStyleOptions = [
    { value: 'narrative', label: 'Narrative' },
    { value: 'bullets', label: 'Bullet-Heavy' },
    { value: 'mixed', label: 'Mixed' },
];

const complexityOptions = [
    { value: 'simple', label: 'Simple (Plain English)' },
    { value: 'intermediate', label: 'Intermediate (Standard)' },
    { value: 'expert', label: 'Expert (Sophisticated)' },
];

const healthFrameworkOptions = [
    { value: 'value_investor_default', label: 'Value Investor Default' },
    { value: 'quality_moat_focus', label: 'Quality & Moat Focus' },
    { value: 'financial_resilience', label: 'Financial Resilience' },
    { value: 'growth_sustainability', label: 'Growth Sustainability' },
    { value: 'user_defined_mix', label: 'User-Defined Mix' },
];

const healthWeightingOptions = [
    { value: 'profitability_margins', label: 'Profitability & Margins' },
    { value: 'cash_flow_conversion', label: 'Cash Flow & Conversion' },
    { value: 'balance_sheet_strength', label: 'Balance Sheet Strength' },
    { value: 'liquidity_near_term_risk', label: 'Liquidity & Near-Term Risk' },
    { value: 'execution_competitiveness', label: 'Execution & Competitiveness' },
];

const healthRiskOptions = [
    { value: 'very_conservative', label: 'Very Conservative' },
    { value: 'moderately_conservative', label: 'Moderately Conservative' },
    { value: 'balanced', label: 'Balanced' },
    { value: 'moderately_lenient', label: 'Moderately Lenient' },
    { value: 'very_lenient', label: 'Very Lenient' },
];

const healthAnalysisDepthOptions = [
    { value: 'headline_only', label: 'Headline Red Flags' },
    { value: 'key_financial_items', label: 'Key Financial Items' },
    { value: 'full_footnote_review', label: 'Full Footnote Review' },
    { value: 'accounting_integrity', label: 'Accounting Integrity' },
    { value: 'forensic_deep_dive', label: 'Forensic Deep Dive' },
];

const healthDisplayOptions = [
    { value: 'score_only', label: '0–100 Score Only' },
    { value: 'score_plus_grade', label: 'Score + Letter Grade' },
    { value: 'score_plus_traffic_light', label: 'Score + Traffic Light' },
    { value: 'score_plus_pillars', label: 'Score + 4 Pillars' },
    { value: 'score_with_narrative', label: 'Score + Narrative' },
];

const loadingStates = [
    { text: "Initializing AI Agent..." },
    { text: "Reading Filing Content..." },
    { text: "Extracting Financial Data..." },
    { text: "Analyzing Risk Factors..." },
    { text: "Computing Health Score..." },
    { text: "Synthesizing Investor Insights..." },
    { text: "Drafting Final Summary..." },
    { text: "Polishing Output..." },
];

export const INVESTOR_PERSONAS = [
    {
        id: 'warren_buffett',
        name: 'Warren Buffett',
        image: '/investors/warren-buffett.png',
        tagline: 'Value, Moat, Free Cash Flow Focus',
        description: 'Folksy clarity, extremely rational, patient, long-term thinker. Prefers simplicity over complexity.',
        prompt: `Role: Warren Buffett.
Personality: Folksy clarity, extremely rational, patient, long-term thinker. Prefers simplicity over complexity. Emphasizes temperament over IQ.
Core Philosophy: Buy wonderful businesses at fair prices; focus on durable competitive advantages (“moats”), high returns on capital, trustworthy management, recurring revenue, and shareholder-aligned incentives.
What He Cares About Most: Consistent free cash flow, ROE without excess leverage, Wide moats (brand, scale, switching costs, network effects), Predictability of earnings over decades, Avoiding capital-intensive, cyclical industries.
How He Would Interpret a Company Summary: “Is this business easy to understand?” “Does it compound reliably over long periods?” “Is management rational and honest?” “Does the valuation offer a margin of safety relative to intrinsic value?”`
    },
    {
        id: 'charlie_munger',
        name: 'Charlie Munger',
        image: '/investors/charlie-munger.webp',
        tagline: 'Rationality, Quality Businesses, Mental Models',
        description: 'Sharp, blunt, and deeply mathematical. Multidisciplinary thinker. Obsessed with incentives and psychology.',
        prompt: `Role: Charlie Munger.
Personality: Sharp, blunt, and deeply mathematical. Multidisciplinary thinker. Obsessed with incentives and psychology.
Core Philosophy: Favor high-quality, high-return businesses even if they appear expensive; focus on long-term competitive dynamics and eliminating stupidity rather than chasing brilliance.
Key Analytical Traits: Latticework of mental models, Inversion (“avoid stupidity first”), Preference for strong, ethical management, Deep skepticism toward hype, emotion, and poor incentives.
How He Views a Summary: “What are the second-order consequences?” “Are incentives aligned, or is this a future train wreck?” “Is this business actually durable, or is it an illusion?” “Does this company reduce friction and deliver true customer value?”`
    },
    {
        id: 'benjamin_graham',
        name: 'Benjamin Graham',
        image: '/investors/benjamin-graham.jpg',
        tagline: 'Margin of Safety, Quantitative Value',
        description: 'Methodical, introverted, discipline-driven, grandfather of value investing.',
        prompt: `Role: Benjamin Graham.
Personality: Methodical, introverted, discipline-driven, grandfather of value investing.
Core Philosophy: Strict intrinsic value calculation, statistical bargains, balance-sheet strength, downside protection.
Key Focus Areas: Net-net valuations, Asset value vs market price, Strong balance sheet liquidation value, Quantitative screens.
How He Reads a Summary: “Is the price unjustifiably low relative to fundamentals?” “Is the downside well protected?”`
    },
    {
        id: 'peter_lynch',
        name: 'Peter Lynch',
        image: '/investors/peter-lynch.webp',
        tagline: 'Growth at a Reasonable Price (GARP)',
        description: 'Energetic, practical, consumer-focused. Believes in understanding what you own deeply.',
        prompt: `Role: Peter Lynch.
Personality: Energetic, practical, consumer-focused. Believes in understanding what you own deeply.
Core Philosophy: Invest in what you know, GARP, earnings growth, business categories (stalwarts, fast-growers, cyclicals).
Primary Interests: PEG ratio, Revenue + earnings growth consistency, Scuttlebutt and on-the-ground observation.
How He Reads a Summary: “Is this growth durable, or hype?” “Does everyday customer behavior validate this?”`
    },
    {
        id: 'ray_dalio',
        name: 'Ray Dalio',
        image: '/investors/ray-dalio.webp',
        tagline: 'Macro-Aware, Risk Parity, Economic Cycles',
        description: 'Systems thinker, bridge-builder, algorithmic decision maker.',
        prompt: `Role: Ray Dalio.
Personality: Systems thinker, bridge-builder, algorithmic decision maker.
Core Philosophy: Economic cycles, credit cycles, diversification, risk balancing, cause-and-effect understanding.
Key Focus Areas: Interest rates, Monetary policy, Debt burden sustainability, Global macro risk.
How He Interprets a Company: “How does macro environment position this business?” “Is it vulnerable to tightening credit or economic contraction?”`
    },
    {
        id: 'cathie_wood',
        name: 'Cathie Wood',
        image: '/investors/cathie-wood.jpg',
        tagline: 'Disruptive Innovation',
        description: 'Visionary, technology-driven, optimistic about exponential change.',
        prompt: `Role: Cathie Wood.
Personality: Visionary, technology-driven, optimistic about exponential change.
Core Philosophy: Invest in disruptive technologies early; tolerate volatility for long-term upside.
Interests: AI, genomics, robotics, energy storage, blockchain, TAM expansion, Innovation velocity.
How She Reads a Summary: “Is this company riding an exponential technology curve?” “How big can this be if disruption succeeds?”`
    },
    {
        id: 'joel_greenblatt',
        name: 'Joel Greenblatt',
        image: '/investors/joel-greenblatt.jpg',
        tagline: 'Magic Formula Value',
        description: 'Pragmatic, efficient, formula-oriented, long-only value with quantitative simplicity.',
        prompt: `Role: Joel Greenblatt.
Personality: Pragmatic, efficient, formula-oriented, long-only value with quantitative simplicity.
Core Philosophy: High returns on capital + low valuation = outperformance.
Key Metrics: EBIT/EV, ROIC.
How He Interprets the Company: “Is this business both cheap and good by the formula?”`
    },
    {
        id: 'john_bogle',
        name: 'John Bogle',
        image: '/investors/john-bogle.jpg',
        tagline: 'Index Investor, Low Costs, Long Horizon',
        description: 'Humble, principled, frugal, anti-speculation.',
        prompt: `Role: John Bogle.
Personality: Humble, principled, frugal, anti-speculation.
Core Philosophy: Markets outperform most investors; keep costs low; think long-term.
Focus Areas: Expense ratios, Diversification, Avoiding speculation.
How He Views Any One Company: “No single stock is predictable—stay diversified unless risk is justified.”`
    },
    {
        id: 'howard_marks',
        name: 'Howard Marks',
        image: '/investors/howard-marks.jpg',
        tagline: 'Cycles, Risk Assessment, Market Psychology',
        description: 'Calm, cycle-aware, contrarian when appropriate, deeply focused on risk.',
        prompt: `Role: Howard Marks.
Personality: Calm, cycle-aware, contrarian when appropriate, deeply focused on risk.
Core Philosophy: Understanding market cycles, investor psychology, and risk asymmetry.
Primary Concerns: Credit cycle, Risk vs reward, Market sentiment extremes.
How He Reads a Summary: “What risks are underestimated?” “Where are we in the cycle?”`
    },
    {
        id: 'bill_ackman',
        name: 'Bill Ackman',
        image: '/investors/bill-ackman.jpg',
        tagline: 'Activist, Catalysts, Concentrated Bets',
        description: 'Bold, assertive, catalyst-driven, activist orientation.',
        prompt: `Role: Bill Ackman.
Personality: Bold, assertive, catalyst-driven, activist orientation.
Core Philosophy: Identify undervalued companies with catalyst-driven upside; intervene to unlock value.
Key Interests: Management failures, Operational turnaround opportunities, Catalysts (spin-offs, restructuring, buybacks, activism).
How He Reads a Summary: “Where is value being left on the table?” “What actionable catalyst can unlock it?”`
    },
];

interface SummaryWizardProps {
    filings: any[];
    selectedFilingId: string;
    onFilingChange: (id: string) => void;
    preferences: SummaryPreferenceFormState;
    onPreferencesChange: (prefs: SummaryPreferenceFormState) => void;
    onGenerate: () => void;
    isGenerating: boolean;
}

export default function SummaryWizard({
    filings = [],
    selectedFilingId,
    onFilingChange,
    preferences,
    onPreferencesChange,
    onGenerate,
    isGenerating,
}: SummaryWizardProps) {
    const [isModalOpen, setIsModalOpen] = useState(false);

    const updatePref = (updates: Partial<SummaryPreferenceFormState>) => {
        onPreferencesChange({ ...preferences, ...updates });
    };

    const updateHealth = (updates: Partial<HealthRatingFormState>) => {
        onPreferencesChange({
            ...preferences,
            healthRating: { ...preferences.healthRating, ...updates }
        });
    };

    const toggleFocusArea = (area: string) => {
        const current = preferences.focusAreas;
        const updated = current.includes(area)
            ? current.filter(a => a !== area)
            : [...current, area];
        updatePref({ focusAreas: updated });
    };

    const handleCustomClick = () => {
        updatePref({ mode: 'custom' });
        setIsModalOpen(true);
    };

    const handleDefaultClick = () => {
        updatePref({ mode: 'default' });
        setIsModalOpen(false);
    };

    const handleModalComplete = () => {
        setIsModalOpen(false);
        onGenerate();
    };

    // Helper to format date safely
    const formatDate = (dateString: string) => {
        if (!dateString) return 'N/A';
        const date = new Date(dateString);
        return isNaN(date.getTime()) ? 'Invalid Date' : date.toLocaleDateString();
    };

    return (
        <div className="w-full">
            <MultiStepLoader
                loadingStates={loadingStates}
                loading={isGenerating}
                duration={2000}
                stopOnLastStep={true}
            />

            <div className="space-y-6">
                <div>
                    <label className="block text-xs font-bold uppercase mb-2">Select Filing</label>
                    <select
                        value={selectedFilingId}
                        onChange={(e) => onFilingChange(e.target.value)}
                        className="w-full p-3 bg-gray-50 dark:bg-black border-2 border-black dark:border-white font-mono text-sm focus:outline-none focus:shadow-[4px_4px_0px_0px_rgba(0,0,0,1)] transition-shadow"
                    >
                        <option value="">-- Choose a filing --</option>
                        {filings.map((f) => (
                            <option key={f.id} value={f.id}>
                                {f.type || f.filing_type} — {formatDate(f.filing_date || f.period_end_date)}
                            </option>
                        ))}
                    </select>
                </div>

                {selectedFilingId && (
                    <div className="space-y-4 pt-4 border-t-2 border-gray-100 dark:border-gray-800">
                        <div className="grid grid-cols-2 gap-4">
                            <button
                                onClick={handleDefaultClick}
                                className={cn(
                                    "p-4 border-2 border-black dark:border-white font-bold uppercase transition-all",
                                    preferences.mode === 'default'
                                        ? "bg-black text-white dark:bg-white dark:text-black shadow-[4px_4px_0px_0px_rgba(128,128,128,1)]"
                                        : "bg-white dark:bg-black hover:bg-gray-50"
                                )}
                            >
                                Default
                            </button>
                            <button
                                onClick={handleCustomClick}
                                className={cn(
                                    "p-4 border-2 border-black dark:border-white font-bold uppercase transition-all",
                                    preferences.mode === 'custom'
                                        ? "bg-black text-white dark:bg-white dark:text-black shadow-[4px_4px_0px_0px_rgba(128,128,128,1)]"
                                        : "bg-white dark:bg-black hover:bg-gray-50"
                                )}
                            >
                                Custom
                            </button>
                        </div>

                        {preferences.mode === 'default' && (
                            <BrutalButton
                                onClick={onGenerate}
                                disabled={isGenerating}
                                className="w-full"
                            >
                                {isGenerating ? 'Generating...' : 'Generate Summary'}
                            </BrutalButton>
                        )}
                        {preferences.mode === 'custom' && (
                            <div className="text-center text-xs text-gray-500 font-mono">
                                Click "Custom" again to configure detailed preferences.
                            </div>
                        )}
                    </div>
                )}
            </div>

            <Modal isOpen={isModalOpen} onClose={() => setIsModalOpen(false)}>
                <Stepper
                    initialStep={1}
                    onFinalStepCompleted={handleModalComplete}
                    backButtonText="Back"
                    nextButtonText="Next"
                    stepCircleContainerClassName="bg-white dark:bg-zinc-900"
                >
                    {/* Step 1: Preferences */}
                    <Step>
                        <div className="space-y-6">
                            <h2 className="text-xl font-black uppercase">1. Customize Output</h2>
                            <div className="space-y-2">
                                <label className="block text-xs font-bold uppercase">Focus Areas</label>
                                <div className="flex flex-wrap gap-2">
                                    {focusAreaOptions.map(area => (
                                        <button
                                            key={area}
                                            onClick={() => toggleFocusArea(area)}
                                            className={cn(
                                                "px-3 py-1.5 text-[10px] font-bold uppercase border border-black dark:border-white transition-all",
                                                preferences.focusAreas.includes(area)
                                                    ? "bg-blue-600 text-white shadow-[2px_2px_0px_0px_rgba(0,0,0,1)]"
                                                    : "bg-white dark:bg-black hover:bg-gray-50"
                                            )}
                                        >
                                            {area}
                                        </button>
                                    ))}
                                </div>
                            </div>

                            <div className="grid grid-cols-2 gap-4">
                                <div className="space-y-2">
                                    <label className="block text-xs font-bold uppercase">Tone</label>
                                    <select
                                        value={preferences.tone}
                                        onChange={(e) => updatePref({ tone: e.target.value as any })}
                                        className="w-full p-2 bg-white dark:bg-black border-2 border-black dark:border-white text-xs font-mono"
                                    >
                                        {toneOptions.map(opt => (
                                            <option key={opt.value} value={opt.value}>{opt.label}</option>
                                        ))}
                                    </select>
                                </div>
                                <div className="space-y-2">
                                    <label className="block text-xs font-bold uppercase">Detail Level</label>
                                    <select
                                        value={preferences.detailLevel}
                                        onChange={(e) => updatePref({ detailLevel: e.target.value as any })}
                                        className="w-full p-2 bg-white dark:bg-black border-2 border-black dark:border-white text-xs font-mono"
                                    >
                                        {detailOptions.map(opt => (
                                            <option key={opt.value} value={opt.value}>{opt.label}</option>
                                        ))}
                                    </select>
                                </div>
                            </div>

                            <div className="space-y-2 pt-2">
                                <label className="block text-xs font-bold uppercase">Complexity Level</label>
                                <div className="grid grid-cols-3 gap-2">
                                    {complexityOptions.map(opt => (
                                        <button
                                            key={opt.value}
                                            onClick={() => updatePref({ complexity: opt.value as any })}
                                            className={cn(
                                                "p-2 text-[10px] font-bold uppercase border border-black dark:border-white transition-all",
                                                preferences.complexity === opt.value
                                                    ? "bg-black text-white dark:bg-white dark:text-black shadow-[2px_2px_0px_0px_rgba(128,128,128,1)]"
                                                    : "bg-white dark:bg-black hover:bg-gray-50"
                                            )}
                                        >
                                            {opt.label}
                                        </button>
                                    ))}
                                </div>
                            </div>

                            <div className="space-y-2 pt-4 border-t border-gray-100 dark:border-gray-800">
                                <div className="flex justify-between items-end mb-2">
                                    <label className="block text-xs font-bold uppercase">Target Length (Words)</label>
                                    <div className="flex items-center border-2 border-black dark:border-white shadow-[2px_2px_0px_0px_rgba(0,0,0,1)] dark:shadow-[2px_2px_0px_0px_rgba(255,255,255,1)] focus-within:shadow-[4px_4px_0px_0px_rgba(0,0,0,1)] dark:focus-within:shadow-[4px_4px_0px_0px_rgba(255,255,255,1)] transition-all bg-white dark:bg-black mr-1">
                                        <input
                                            type="number"
                                            value={preferences.targetLength}
                                            onChange={(e) => {
                                                const val = parseInt(e.target.value);
                                                if (!isNaN(val)) updatePref({ targetLength: val });
                                            }}
                                            className="w-20 p-2 text-right font-mono text-sm font-bold bg-transparent outline-none border-none [appearance:textfield] [&::-webkit-outer-spin-button]:appearance-none [&::-webkit-inner-spin-button]:appearance-none"
                                        />
                                        <div className="h-4 w-0.5 bg-gray-200 dark:bg-gray-800 mx-1" />
                                        <span className="pr-3 pl-1 text-[10px] font-bold uppercase text-gray-500 select-none">
                                            WORDS
                                        </span>
                                    </div>
                                </div>
                                <BrutalSlider
                                    value={preferences.targetLength}
                                    min={200}
                                    max={5000}
                                    step={50}
                                    onChange={(val) => updatePref({ targetLength: val })}
                                />
                            </div>
                        </div>
                    </Step>

                    {/* Step 2: Health Check */}
                    <Step>
                        <div className="space-y-6">
                            <h2 className="text-xl font-black uppercase">2. Health Analysis</h2>
                            <p className="text-sm text-gray-600 dark:text-gray-400">
                                Would you like to include a detailed health score analysis in your summary?
                            </p>
                            <div className="grid grid-cols-2 gap-4">
                                <button
                                    onClick={() => updatePref({ includeHealthScore: true })}
                                    className={cn(
                                        "p-4 border-2 border-black dark:border-white font-bold uppercase transition-all flex flex-col items-center gap-2",
                                        preferences.includeHealthScore
                                            ? "bg-green-400 text-black shadow-[4px_4px_0px_0px_rgba(0,0,0,1)]"
                                            : "bg-white dark:bg-black hover:bg-gray-50"
                                    )}
                                >
                                    <span className="text-2xl">✓</span>
                                    Yes, Include It
                                </button>
                                <button
                                    onClick={() => updatePref({ includeHealthScore: false })}
                                    className={cn(
                                        "p-4 border-2 border-black dark:border-white font-bold uppercase transition-all flex flex-col items-center gap-2",
                                        !preferences.includeHealthScore
                                            ? "bg-red-400 text-black shadow-[4px_4px_0px_0px_rgba(0,0,0,1)]"
                                            : "bg-white dark:bg-black hover:bg-gray-50"
                                    )}
                                >
                                    <span className="text-2xl">✕</span>
                                    No, Skip It
                                </button>
                            </div>
                        </div>
                    </Step>

                    {/* Step 3: Health Configuration (Conditional) */}
                    {preferences.includeHealthScore && (
                        <Step>
                            <div className="space-y-6">
                                <h2 className="text-xl font-black uppercase">3. Configure Health Score</h2>
                                <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                                    <div>
                                        <label className="block text-[10px] font-bold uppercase mb-1">Framework</label>
                                        <select
                                            value={preferences.healthRating.framework}
                                            onChange={(e) => updateHealth({ framework: e.target.value as any })}
                                            className="w-full p-2 bg-white dark:bg-black border border-black dark:border-white text-xs font-mono"
                                        >
                                            {healthFrameworkOptions.map(opt => (
                                                <option key={opt.value} value={opt.value}>{opt.label}</option>
                                            ))}
                                        </select>
                                    </div>
                                    <div>
                                        <label className="block text-[10px] font-bold uppercase mb-1">Weighting</label>
                                        <select
                                            value={preferences.healthRating.weighting}
                                            onChange={(e) => updateHealth({ weighting: e.target.value as any })}
                                            className="w-full p-2 bg-white dark:bg-black border border-black dark:border-white text-xs font-mono"
                                        >
                                            {healthWeightingOptions.map(opt => (
                                                <option key={opt.value} value={opt.value}>{opt.label}</option>
                                            ))}
                                        </select>
                                    </div>
                                    <div>
                                        <label className="block text-[10px] font-bold uppercase mb-1">Risk Tolerance</label>
                                        <select
                                            value={preferences.healthRating.riskTolerance}
                                            onChange={(e) => updateHealth({ riskTolerance: e.target.value as any })}
                                            className="w-full p-2 bg-white dark:bg-black border border-black dark:border-white text-xs font-mono"
                                        >
                                            {healthRiskOptions.map(opt => (
                                                <option key={opt.value} value={opt.value}>{opt.label}</option>
                                            ))}
                                        </select>
                                    </div>
                                    <div>
                                        <label className="block text-[10px] font-bold uppercase mb-1">Analysis Depth</label>
                                        <select
                                            value={preferences.healthRating.analysisDepth}
                                            onChange={(e) => updateHealth({ analysisDepth: e.target.value as any })}
                                            className="w-full p-2 bg-white dark:bg-black border border-black dark:border-white text-xs font-mono"
                                        >
                                            {healthAnalysisDepthOptions.map(opt => (
                                                <option key={opt.value} value={opt.value}>{opt.label}</option>
                                            ))}
                                        </select>
                                    </div>
                                </div>
                            </div>
                        </Step>
                    )}

                    {/* Step 4: Investor Persona Selection */}
                    <Step>
                        <div className="space-y-6">
                            <h2 className="text-xl font-black uppercase">4. Select Investor Persona</h2>
                            <p className="text-sm text-gray-600 dark:text-gray-400">
                                Choose an investor lens to analyze this company.
                            </p>
                            <div className="grid grid-cols-2 gap-4 max-h-[400px] overflow-y-auto pr-2">
                                {INVESTOR_PERSONAS.map((persona) => (
                                    <button
                                        key={persona.id}
                                        onClick={() => updatePref({ selectedPersona: preferences.selectedPersona === persona.id ? null : persona.id })}
                                        className={cn(
                                            "relative p-4 border-2 border-black dark:border-white text-left transition-all group hover:bg-gray-50 dark:hover:bg-zinc-900",
                                            preferences.selectedPersona === persona.id
                                                ? "bg-black text-white dark:bg-white dark:text-black shadow-[4px_4px_0px_0px_rgba(0,0,0,1)] dark:shadow-[4px_4px_0px_0px_rgba(255,255,255,1)]"
                                                : "bg-white dark:bg-black"
                                        )}
                                    >
                                        <div className="flex items-start gap-3">
                                            <div className="w-12 h-12 rounded-full overflow-hidden flex-shrink-0 border border-current">
                                                <img src={persona.image} alt={persona.name} className="w-full h-full object-cover" />
                                            </div>
                                            <div>
                                                <h3 className="font-bold uppercase text-sm leading-tight">{persona.name}</h3>
                                                <p className="text-[10px] font-mono mt-1 opacity-80 leading-tight">{persona.tagline}</p>
                                            </div>
                                        </div>
                                        {preferences.selectedPersona === persona.id && (
                                            <div className="absolute top-2 right-2 text-green-500">
                                                ✓
                                            </div>
                                        )}
                                    </button>
                                ))}
                            </div>
                        </div>
                    </Step>

                    {/* Step 5: Additional Instructions (Custom Only) */}
                    {preferences.mode === 'custom' && (
                        <Step>
                            <div className="space-y-6">
                                <h2 className="text-xl font-black uppercase">5. Additional Instructions</h2>
                                <p className="text-sm text-gray-600 dark:text-gray-400">
                                    Any specific requests or context for the AI?
                                </p>
                                <textarea
                                    value={preferences.investorFocus}
                                    onChange={(e) => updatePref({ investorFocus: e.target.value })}
                                    placeholder="E.g., Focus on the impact of recent regulatory changes..."
                                    className="w-full h-32 p-4 bg-white dark:bg-black border-2 border-black dark:border-white font-mono text-sm focus:outline-none focus:shadow-[4px_4px_0px_0px_rgba(0,0,0,1)] transition-shadow resize-none"
                                />
                            </div>
                        </Step>
                    )}

                    {/* Final Step: Review */}
                    <Step>
                        <div className="space-y-6">
                            <h2 className="text-xl font-black uppercase">6. Ready to Generate</h2>

                            <div className="border-2 border-black dark:border-white p-6 space-y-4 bg-white dark:bg-black shadow-[4px_4px_0px_0px_rgba(0,0,0,1)]">
                                <div className="flex justify-between items-center border-b border-gray-200 dark:border-gray-800 pb-2">
                                    <span className="font-bold uppercase text-sm">Mode</span>
                                    <span className="font-mono text-sm">{preferences.mode}</span>
                                </div>

                                {preferences.mode === 'custom' && (
                                    <>
                                        <div className="flex justify-between items-center border-b border-gray-200 dark:border-gray-800 pb-2">
                                            <span className="font-bold uppercase text-sm">Focus Areas</span>
                                            <span className="font-mono text-sm text-right">
                                                {preferences.focusAreas.length > 0 ? preferences.focusAreas.join(', ') : 'None'}
                                            </span>
                                        </div>
                                        <div className="flex justify-between items-center border-b border-gray-200 dark:border-gray-800 pb-2">
                                            <span className="font-bold uppercase text-sm">Tone</span>
                                            <span className="font-mono text-sm">{preferences.tone}</span>
                                        </div>
                                        <div className="flex justify-between items-center border-b border-gray-200 dark:border-gray-800 pb-2">
                                            <span className="font-bold uppercase text-sm">Health Score</span>
                                            <span className={cn("font-mono text-sm font-bold", preferences.includeHealthScore ? "text-green-600" : "text-gray-400")}>
                                                {preferences.includeHealthScore ? 'INCLUDED' : 'EXCLUDED'}
                                            </span>
                                        </div>
                                        <div className="flex justify-between items-center border-b border-gray-200 dark:border-gray-800 pb-2">
                                            <span className="font-bold uppercase text-sm">Investor Persona</span>
                                            <span className="font-mono text-sm font-bold text-blue-500">
                                                {preferences.selectedPersona ? INVESTOR_PERSONAS.find(p => p.id === preferences.selectedPersona)?.name : 'None'}
                                            </span>
                                        </div>
                                        {preferences.investorFocus && (
                                            <div className="flex flex-col gap-1 border-b border-gray-200 dark:border-gray-800 pb-2">
                                                <span className="font-bold uppercase text-sm">Instructions</span>
                                                <span className="font-mono text-xs text-gray-600 dark:text-gray-400 line-clamp-2">
                                                    {preferences.investorFocus}
                                                </span>
                                            </div>
                                        )}
                                        <div className="flex justify-between items-center border-b border-gray-200 dark:border-gray-800 pb-2">
                                            <span className="font-bold uppercase text-sm">Target Length</span>
                                            <span className="font-mono text-sm">{preferences.targetLength} words</span>
                                        </div>
                                        <div className="flex justify-between items-center border-b border-gray-200 dark:border-gray-800 pb-2">
                                            <span className="font-bold uppercase text-sm">Complexity</span>
                                            <span className="font-mono text-sm capitalize">{preferences.complexity}</span>
                                        </div>
                                    </>
                                )}

                                <div className="pt-2">
                                    <p className="text-xs text-gray-500">
                                        Click "Complete" to start the AI analysis. This process typically takes 10-20 seconds.
                                    </p>
                                </div>
                            </div>
                        </div>
                    </Step>
                </Stepper>
            </Modal>
        </div >
    );
}
