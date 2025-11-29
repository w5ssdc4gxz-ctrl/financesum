"use client";

import Link from "next/link";
import { useState } from "react";
import { HiArrowLongRight } from "react-icons/hi2";
import { cn } from "@/lib/utils";
import { GradientButton } from "@/components/ui/GradientButton";

type NavItem = {
  label: string;
  href: string;
  tag?: string;
};

type NavColumn = {
  title: string;
  items: NavItem[];
};

const navColumns: NavColumn[] = [
  {
    title: "Platform",
    items: [
      { label: "AI Filing Agents", href: "/dashboard", tag: "NEW" },
      { label: "Memo Composer", href: "/dashboard" },
      { label: "KPI Explorer", href: "/dashboard" },
      { label: "Risk Engine", href: "/dashboard" },
      { label: "Scenario Studio", href: "/dashboard" },
      { label: "Compliance Guard", href: "/dashboard" },
    ],
  },
  {
    title: "Audience",
    items: [
      { label: "Institutional desks", href: "/#journey" },
      { label: "Wealth advisors", href: "/#journey" },
      { label: "Fintech teams", href: "/#journey" },
      { label: "IR leaders", href: "/#journey" },
    ],
  },
  {
    title: "Resources",
    items: [
      { label: "Product tour", href: "#app-demo" },
      { label: "Case studies", href: "/#journey" },
      { label: "Docs & API", href: "/dashboard" },
      { label: "Templates", href: "/dashboard" },
      { label: "Webinars", href: "/dashboard" },
      { label: "SEC playbook", href: "/dashboard" },
    ],
  },
  {
    title: "Developers",
    items: [
      { label: "API reference", href: "/dashboard" },
      { label: "SDK & CLI", href: "/dashboard" },
      { label: "Webhooks", href: "/dashboard" },
      { label: "Status", href: "/dashboard" },
      { label: "Changelog", href: "/dashboard" },
    ],
  },
  {
    title: "Company",
    items: [
      { label: "About FinanceSum", href: "/#journey" },
      { label: "Compare plans", href: "/compare" },
      { label: "Product updates", href: "/#app-demo" },
      { label: "Customer stories", href: "/dashboard" },
    ],
  },
];

export default function MegaFooter() {
  console.log("MegaFooter rendering");
  const [active, setActive] = useState<string | null>(null);
  const hovering = Boolean(active);

  const mainLinks = [
    { label: "Platform", href: "#journey" },
    { label: "Audience", href: "#personas" },
    { label: "Resources", href: "#visuals" },
    { label: "Developers", href: "#personas" },
    { label: "Company", href: "#visuals" },
  ];

  return (
    <section className="relative mt-20 border-t border-white/10 bg-[#050015] text-white overflow-hidden">
      <div className="mx-auto max-w-7xl px-6 py-24 sm:px-10 lg:px-16 relative z-10">
        <div className="flex flex-col lg:flex-row items-center justify-between gap-12">

          {/* Horizontal Links */}
          <div className="flex flex-wrap justify-center lg:justify-start gap-8 md:gap-12">
            {mainLinks.map((item) => {
              const isActive = active === item.label;
              const dimmed = hovering && !isActive;

              return (
                <Link
                  key={item.label}
                  href={item.href}
                  onMouseEnter={() => setActive(item.label)}
                  onMouseLeave={() => setActive(null)}
                  className={cn(
                    "group flex items-center gap-2 text-2xl font-semibold transition-all duration-300",
                    dimmed && "text-white/30 blur-[0.5px]",
                    !hovering && "text-white/70",
                    isActive && "text-white scale-105"
                  )}
                >
                  <span>{item.label}</span>
                  <HiArrowLongRight
                    className="text-2xl text-primary-400 opacity-0 -translate-x-2 transition-all duration-300 group-hover:opacity-100 group-hover:translate-x-0"
                  />
                </Link>
              );
            })}
          </div>

          {/* CTA Button */}
          <div className="flex-shrink-0">
            <Link href="/signup">
              <GradientButton className="text-lg px-8 py-4">Start for free</GradientButton>
            </Link>
          </div>
        </div>

        <div className="mt-24 flex flex-col md:flex-row justify-between items-center text-xs text-white/30 uppercase tracking-[0.2em] gap-4">
          <p>© {new Date().getFullYear()} FinanceSum. All rights reserved.</p>
        </div>
      </div>

      {/* Large Background Logo */}
      <div className="absolute bottom-0 left-1/2 -translate-x-1/2 translate-y-[15%] w-full text-center pointer-events-none select-none z-0">
        <h1 className="text-[12vw] md:text-[15vw] font-black tracking-tighter text-white/5 leading-none whitespace-nowrap">
          FINANCESUM
        </h1>
      </div>
    </section>
  );
}

