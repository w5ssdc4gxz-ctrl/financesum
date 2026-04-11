"use client";

import { useState, useEffect, useRef } from "react";
import { motion, AnimatePresence } from "framer-motion";

interface FAQItem {
  id: number;
  question: string;
  answer: string;
}

const faqItems: FAQItem[] = [
  {
    id: 1,
    question: "How does FinanceSum analyze SEC filings?",
    answer:
      "We use advanced AI to parse 10-K, 10-Q, and 8-K filings, extracting key financial metrics, risk factors, and business insights. Our system highlights the most important changes so you don't have to read hundreds of pages.",
  },
  {
    id: 2,
    question: "How long does it take to get a summary?",
    answer:
      "Most filings are analyzed within 2-3 minutes. Complex annual reports may take up to 5 minutes. You'll receive a notification when your summary is ready.",
  },
  {
    id: 3,
    question: "What companies can I analyze?",
    answer:
      "You can analyze any publicly traded company that files with the SEC. This includes all US-listed stocks, ADRs, and foreign companies registered with the SEC.",
  },
  {
    id: 4,
    question: "Is my data and watchlist private?",
    answer:
      "Absolutely. Your watchlist and analysis history are completely private and encrypted. We never share your data with third parties or other users.",
  },
  {
    id: 5,
    question: "Can I export my summaries?",
    answer:
      "Yes! You can export summaries as PDF reports, copy key insights to clipboard, or share specific analyses via secure links with your team.",
  },
  {
    id: 6,
    question: "What's included in the free plan?",
    answer:
      "The free plan includes a 1-summary trial so you can test FinanceSum. Upgrade anytime for 100 summaries per month and advanced features.",
  },
];

// Typing dots component
function TypingDots() {
  return (
    <div className="flex items-center gap-1.5 px-4 py-3">
      {[0, 1, 2].map((i) => (
        <motion.span
          key={i}
          className="w-2.5 h-2.5 bg-gray-400 rounded-full"
          animate={{
            opacity: [0.4, 1, 0.4],
            scale: [0.85, 1, 0.85],
          }}
          transition={{
            duration: 1,
            repeat: Infinity,
            delay: i * 0.2,
            ease: "easeInOut",
          }}
        />
      ))}
    </div>
  );
}

export function FAQChat() {
  const [selectedQuestion, setSelectedQuestion] = useState<FAQItem | null>(null);
  const [isLoading, setIsLoading] = useState(false);
  const [displayedAnswer, setDisplayedAnswer] = useState("");
  const [isTyping, setIsTyping] = useState(false);
  const chatContainerRef = useRef<HTMLDivElement>(null);

  // Handle question click
  const handleQuestionClick = (item: FAQItem) => {
    // Reset states
    setDisplayedAnswer("");
    setIsTyping(false);
    setSelectedQuestion(item);
    setIsLoading(true);

    // Simulate loading delay (1.5 seconds)
    setTimeout(() => {
      setIsLoading(false);
      setIsTyping(true);
    }, 1500);
  };

  // Typewriter effect
  useEffect(() => {
    if (!isTyping || !selectedQuestion) return;

    let currentIndex = 0;
    const answer = selectedQuestion.answer;

    const interval = setInterval(() => {
      if (currentIndex < answer.length) {
        setDisplayedAnswer(answer.slice(0, currentIndex + 1));
        currentIndex++;
      } else {
        setIsTyping(false);
        clearInterval(interval);
      }
    }, 25); // 25ms per character for smooth typing

    return () => clearInterval(interval);
  }, [isTyping, selectedQuestion]);

  // Auto-scroll chat container when new content appears
  useEffect(() => {
    if (chatContainerRef.current) {
      chatContainerRef.current.scrollTop = chatContainerRef.current.scrollHeight;
    }
  }, [selectedQuestion, displayedAnswer, isLoading]);

  return (
    <section className="py-16 md:py-24 bg-white dark:bg-zinc-950 border-b border-zinc-200 dark:border-zinc-800">
      <div className="container-tight">
        {/* Section Header */}
        <motion.div
          initial={{ opacity: 0, y: 10 }}
          whileInView={{ opacity: 1, y: 0 }}
          viewport={{ once: true }}
          transition={{ duration: 0.8, ease: [0.25, 1, 0.5, 1] }}
          className="text-center mb-16"
        >
          <span className="text-xs font-bold tracking-widest text-zinc-400 uppercase mb-4 block">
            Got Questions?
          </span>
          <h2 className="text-5xl md:text-7xl font-black tracking-tighter text-black dark:text-white mb-6 uppercase">
            We have answers
          </h2>
          <p className="text-lg text-zinc-500 font-medium max-w-2xl mx-auto">
            Click on any question below to see how FinanceSum can help you.
          </p>
        </motion.div>

        {/* Chat Window */}
        <motion.div
          initial={{ opacity: 0, y: 20 }}
          whileInView={{ opacity: 1, y: 0 }}
          viewport={{ once: true }}
          transition={{ duration: 0.8, delay: 0.2, ease: [0.25, 1, 0.5, 1] }}
          className="relative max-w-3xl mx-auto mb-16"
        >
          {/* Chat Container */}
          <div className="bg-white dark:bg-zinc-950 border border-zinc-200 dark:border-zinc-800 shadow-sm overflow-hidden">
            {/* Chat Header */}
            <div className="px-6 py-5 border-b border-zinc-200 dark:border-zinc-800 bg-zinc-50 dark:bg-zinc-900">
              <div className="flex items-center gap-4">
                <div className="w-10 h-10 bg-black dark:bg-white flex items-center justify-center">
                  <svg
                    className="w-5 h-5 text-white dark:text-black"
                    fill="none"
                    viewBox="0 0 24 24"
                    stroke="currentColor"
                  >
                    <path
                      strokeLinecap="round"
                      strokeLinejoin="round"
                      strokeWidth={2}
                      d="M8 10h.01M12 10h.01M16 10h.01M9 16H5a2 2 0 01-2-2V6a2 2 0 012-2h14a2 2 0 012 2v8a2 2 0 01-2 2h-5l-5 5v-5z"
                    />
                  </svg>
                </div>
                <div>
                  <h3 className="text-sm font-bold tracking-widest uppercase text-black dark:text-white">FinanceSum Assistant</h3>
                  <p className="text-xs text-zinc-500 font-medium">Always here to help</p>
                </div>
                <div className="ml-auto flex items-center gap-2">
                  <span className="w-1.5 h-1.5 bg-black dark:bg-white animate-pulse" />
                  <span className="text-xs font-bold tracking-widest uppercase text-zinc-500">Online</span>
                </div>
              </div>
            </div>

            {/* Chat Messages Area */}
            <div
              ref={chatContainerRef}
              className="h-[320px] overflow-y-auto px-6 py-8 space-y-6 scroll-smooth bg-white dark:bg-zinc-950"
            >
              {/* Initial state - prompt to select a question */}
              {!selectedQuestion && (
                <motion.div
                  initial={{ opacity: 0, y: 10 }}
                  animate={{ opacity: 1, y: 0 }}
                  className="flex justify-start"
                >
                  <div className="bg-zinc-100 dark:bg-zinc-900 text-black dark:text-white border border-zinc-200 dark:border-zinc-800 px-6 py-4 max-w-[85%]">
                    <p className="text-sm font-medium leading-relaxed">
                      Hi there! I&apos;m here to answer your questions about FinanceSum. Click any
                      question below to get started.
                    </p>
                  </div>
                </motion.div>
              )}

              {/* Selected Question (User Bubble) */}
              <AnimatePresence mode="wait">
                {selectedQuestion && (
                  <motion.div
                    key={`question-${selectedQuestion.id}`}
                    initial={{ opacity: 0, y: 10 }}
                    animate={{ opacity: 1, y: 0 }}
                    exit={{ opacity: 0, y: -10 }}
                    transition={{ duration: 0.2 }}
                    className="flex justify-end"
                  >
                    <div className="bg-black dark:bg-white text-white dark:text-black px-6 py-4 max-w-[85%] shadow-sm border border-black dark:border-white">
                      <p className="text-sm font-bold leading-relaxed">{selectedQuestion.question}</p>
                    </div>
                  </motion.div>
                )}
              </AnimatePresence>

              {/* Loading Dots */}
              <AnimatePresence>
                {isLoading && (
                  <motion.div
                    initial={{ opacity: 0, y: 10 }}
                    animate={{ opacity: 1, y: 0 }}
                    exit={{ opacity: 0 }}
                    transition={{ duration: 0.2 }}
                    className="flex justify-start"
                  >
                    <div className="bg-zinc-100 dark:bg-zinc-900 border border-zinc-200 dark:border-zinc-800">
                      <TypingDots />
                    </div>
                  </motion.div>
                )}
              </AnimatePresence>

              {/* Answer (AI Bubble) */}
              <AnimatePresence>
                {displayedAnswer && !isLoading && (
                  <motion.div
                    initial={{ opacity: 0, y: 10 }}
                    animate={{ opacity: 1, y: 0 }}
                    transition={{ duration: 0.2 }}
                    className="flex justify-start"
                  >
                    <div className="bg-zinc-100 dark:bg-zinc-900 border border-zinc-200 dark:border-zinc-800 text-black dark:text-white px-6 py-4 max-w-[85%]">
                      <p className="text-sm font-medium leading-relaxed">
                        {displayedAnswer}
                        {isTyping && (
                          <motion.span
                            className="inline-block w-2 h-4 bg-black dark:bg-white ml-1 align-middle"
                            animate={{ opacity: [1, 0] }}
                            transition={{ duration: 0.5, repeat: Infinity }}
                          />
                        )}
                      </p>
                    </div>
                  </motion.div>
                )}
              </AnimatePresence>
            </div>
          </div>
        </motion.div>

        {/* Question Bubbles Grid */}
        <motion.div
          initial={{ opacity: 0, y: 20 }}
          whileInView={{ opacity: 1, y: 0 }}
          viewport={{ once: true }}
          transition={{ duration: 0.8, delay: 0.4, ease: [0.25, 1, 0.5, 1] }}
          className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-0 max-w-4xl mx-auto border-t border-l border-zinc-200 dark:border-zinc-800"
        >
          {faqItems.map((item, index) => (
            <motion.button
              key={item.id}
              onClick={() => handleQuestionClick(item)}
              disabled={isLoading || isTyping}
              initial={{ opacity: 0 }}
              whileInView={{ opacity: 1 }}
              viewport={{ once: true }}
              transition={{
                duration: 0.5,
                delay: 0.1 * index,
              }}
              className={`
                relative text-left px-6 py-5 border-r border-b border-zinc-200 dark:border-zinc-800 transition-colors duration-200
                ${selectedQuestion?.id === item.id
                  ? "bg-black text-white dark:bg-white dark:text-black"
                  : "bg-white dark:bg-zinc-950 text-black dark:text-white hover:bg-zinc-100 dark:hover:bg-zinc-900"
                }
                ${(isLoading || isTyping) && selectedQuestion?.id !== item.id ? "opacity-30 cursor-not-allowed" : "cursor-pointer"}
              `}
            >
              <span className="text-sm font-bold leading-snug tracking-tight">{item.question}</span>
            </motion.button>
          ))}
        </motion.div>
      </div>
    </section>
  );
}

export default FAQChat;
