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
      "The free plan includes 5 filing analyses per month, basic summaries, and access to our filings database. Upgrade anytime for unlimited analyses and advanced features.",
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
    <section className="py-16 md:py-24 bg-gradient-to-b from-secondary/30 to-white">
      <div className="container-tight">
        {/* Section Header */}
        <motion.div
          initial={{ opacity: 0, y: 30 }}
          whileInView={{ opacity: 1, y: 0 }}
          viewport={{ once: true }}
          transition={{ duration: 0.8, ease: [0.25, 0.46, 0.45, 0.94] }}
          className="text-center mb-12"
        >
          <span className="text-sm font-medium text-[#0015ff] uppercase tracking-wider mb-4 block">
            Got Questions?
          </span>
          <h2 className="text-4xl sm:text-5xl md:text-6xl font-bold tracking-tight text-foreground mb-6 font-serif italic">
            We have answers
          </h2>
          <p className="text-lg text-muted-foreground max-w-2xl mx-auto">
            Click on any question below to see how FinanceSum can help you.
          </p>
        </motion.div>

        {/* Chat Window */}
        <motion.div
          initial={{ opacity: 0, y: 40 }}
          whileInView={{ opacity: 1, y: 0 }}
          viewport={{ once: true }}
          transition={{ duration: 0.8, delay: 0.2, ease: [0.25, 0.46, 0.45, 0.94] }}
          className="relative max-w-3xl mx-auto mb-10"
        >
          {/* Chat Container */}
          <div className="bg-white/80 backdrop-blur-xl rounded-3xl shadow-elevated border border-gray-100 overflow-hidden">
            {/* Chat Header */}
            <div className="px-6 py-4 border-b border-gray-100 bg-white/50">
              <div className="flex items-center gap-3">
                <div className="w-10 h-10 rounded-full bg-gradient-to-br from-[#0015ff] to-[#7c3aed] flex items-center justify-center">
                  <svg
                    className="w-5 h-5 text-white"
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
                  <h3 className="font-semibold text-foreground">FinanceSum Assistant</h3>
                  <p className="text-xs text-muted-foreground">Always here to help</p>
                </div>
                <div className="ml-auto flex items-center gap-1.5">
                  <span className="w-2 h-2 bg-green-500 rounded-full animate-pulse" />
                  <span className="text-xs text-muted-foreground">Online</span>
                </div>
              </div>
            </div>

            {/* Chat Messages Area */}
            <div
              ref={chatContainerRef}
              className="h-[320px] overflow-y-auto px-6 py-6 space-y-4 scroll-smooth"
            >
              {/* Initial state - prompt to select a question */}
              {!selectedQuestion && (
                <motion.div
                  initial={{ opacity: 0, y: 10 }}
                  animate={{ opacity: 1, y: 0 }}
                  className="flex justify-start"
                >
                  <div className="bg-gray-100 text-foreground rounded-2xl rounded-tl-md px-5 py-3.5 max-w-[85%]">
                    <p className="text-[15px] leading-relaxed">
                      Hi there! I'm here to answer your questions about FinanceSum. Click any
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
                    initial={{ opacity: 0, y: 20, scale: 0.95 }}
                    animate={{ opacity: 1, y: 0, scale: 1 }}
                    exit={{ opacity: 0, y: -10 }}
                    transition={{ duration: 0.3, ease: [0.25, 0.46, 0.45, 0.94] }}
                    className="flex justify-end"
                  >
                    <div className="bg-[#0015ff] text-white rounded-2xl rounded-tr-md px-5 py-3.5 max-w-[85%] shadow-lg">
                      <p className="text-[15px] leading-relaxed">{selectedQuestion.question}</p>
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
                    <div className="bg-gray-100 rounded-2xl rounded-tl-md">
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
                    <div className="bg-gray-100 text-foreground rounded-2xl rounded-tl-md px-5 py-3.5 max-w-[85%]">
                      <p className="text-[15px] leading-relaxed">
                        {displayedAnswer}
                        {isTyping && (
                          <motion.span
                            className="inline-block w-0.5 h-4 bg-[#0015ff] ml-0.5 align-middle"
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
          initial={{ opacity: 0, y: 30 }}
          whileInView={{ opacity: 1, y: 0 }}
          viewport={{ once: true }}
          transition={{ duration: 0.8, delay: 0.4, ease: [0.25, 0.46, 0.45, 0.94] }}
          className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-3 max-w-4xl mx-auto"
        >
          {faqItems.map((item, index) => (
            <motion.button
              key={item.id}
              onClick={() => handleQuestionClick(item)}
              disabled={isLoading || isTyping}
              initial={{ opacity: 0, y: 20 }}
              whileInView={{ opacity: 1, y: 0 }}
              viewport={{ once: true }}
              transition={{
                duration: 0.5,
                delay: 0.1 * index,
                ease: [0.25, 0.46, 0.45, 0.94],
              }}
              whileHover={{
                y: -4,
                scale: 1.02,
                transition: { duration: 0.2 },
              }}
              whileTap={{ scale: 0.98 }}
              className={`
                relative text-left px-5 py-4 rounded-2xl border transition-all duration-200
                ${
                  selectedQuestion?.id === item.id
                    ? "bg-[#0015ff] text-white border-[#0015ff] shadow-lg"
                    : "bg-white hover:bg-gray-50 border-gray-200 text-foreground shadow-sm hover:shadow-md hover:border-gray-300"
                }
                ${(isLoading || isTyping) && selectedQuestion?.id !== item.id ? "opacity-50 cursor-not-allowed" : "cursor-pointer"}
              `}
            >
              <span className="text-sm font-medium leading-snug">{item.question}</span>
            </motion.button>
          ))}
        </motion.div>
      </div>
    </section>
  );
}

export default FAQChat;
