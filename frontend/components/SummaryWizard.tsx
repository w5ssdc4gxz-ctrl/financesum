import React, { useState } from 'react';
import { motion } from 'framer-motion';
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

                            <div className="space-y-2 pt-2">
                                <BrutalSlider
                                    label="Target Length"
                                    value={preferences.targetLength}
                                    min={200}
                                    max={5000}
                                    step={100}
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
                                    <div className="col-span-full">
                                        <label className="block text-[10px] font-bold uppercase mb-1">Display Style</label>
                                        <select
                                            value={preferences.healthRating.displayStyle}
                                            onChange={(e) => updateHealth({ displayStyle: e.target.value as any })}
                                            className="w-full p-2 bg-white dark:bg-black border border-black dark:border-white text-xs font-mono"
                                        >
                                            {healthDisplayOptions.map(opt => (
                                                <option key={opt.value} value={opt.value}>{opt.label}</option>
                                            ))}
                                        </select>
                                    </div>
                                </div>
                            </div>
                        </Step>
                    )}

                    {/* Step 4: Additional Instructions (Custom Only) */}
                    {preferences.mode === 'custom' && (
                        <Step>
                            <div className="space-y-6">
                                <h2 className="text-xl font-black uppercase">4. Additional Instructions</h2>
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
                            <h2 className="text-xl font-black uppercase">5. Ready to Generate</h2>

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
        </div>
    );
}
