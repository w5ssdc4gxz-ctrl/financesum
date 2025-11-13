"use client";

import Link from "next/link";
import { useState } from "react";
import { HiArrowLongRight } from "react-icons/hi2";

import { cn } from "@/lib/utils";

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
      { label: "Case studies", href: "#journey" },
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
];

const companyLinks: NavItem[] = [
  { label: "About FinanceSum", href: "/#journey" },
  { label: "Compare plans", href: "/compare" },
  { label: "Product updates", href: "/#app-demo" },
  { label: "Customer stories", href: "/dashboard" },
];

export default function MegaFooter() {
  const [active, setActive] = useState<string | null>(null);
  const hovering = Boolean(active);

  return (
    <section className="relative mt-20">
      <div className="overflow-hidden rounded-[48px] border border-white/10 bg-[#05000d] px-6 py-16 text-white shadow-[0_40px_140px_rgba(5,0,21,0.6)] sm:px-10 lg:px-16">
        <div className="relative flex flex-col gap-14 lg:flex-row">
          <div className="flex-1">
            <div className="grid gap-10 sm:grid-cols-2 lg:grid-cols-4">
              {navColumns.map((column, columnIndex) => (
                <div
                  key={column.title}
                  className={cn(
                    "space-y-4 border-white/10",
                    columnIndex > 0 && "lg:border-l lg:pl-8",
                  )}
                >
                  <p className="text-xs font-semibold uppercase tracking-[0.4em] text-white/40">
                    {column.title}
                  </p>
                  <div className="space-y-1">
                    {column.items.map((item) => {
                      const id = `${column.title}-${item.label}`;
                      const isActive = active === id;
                      const dimmed = hovering && !isActive;

                      return (
                        <Link
                          key={id}
                          href={item.href}
                          onMouseEnter={() => setActive(id)}
                          onMouseLeave={() => setActive(null)}
                          className={cn(
                            "group flex items-center justify-between gap-4 py-1 text-xl font-semibold transition-all duration-200",
                            dimmed && "text-white/15",
                            !hovering && "text-white/70",
                            isActive && "text-white drop-shadow-[0_0_20px_rgba(255,255,255,0.35)]",
                          )}
                        >
                          <span className="flex items-center gap-3">
                            {item.label}
                            {item.tag && (
                              <span className="rounded-full border border-white/30 px-2 py-0.5 text-[10px] font-semibold uppercase tracking-[0.2em] text-white/80">
                                {item.tag}
                              </span>
                            )}
                          </span>
                          <HiArrowLongRight
                            className={cn(
                              "text-lg transition-all duration-200",
                              isActive
                                ? "text-primary-200 opacity-100"
                                : "opacity-0 text-white/40",
                              isActive && "translate-x-1",
                            )}
                          />
                        </Link>
                      );
                    })}
                  </div>
                </div>
              ))}
            </div>
          </div>

          <div className="w-full max-w-sm space-y-6 rounded-3xl border border-white/10 bg-white/5 p-8 backdrop-blur">
            <p className="text-xs font-semibold uppercase tracking-[0.4em] text-white/50">
              Company
            </p>
            <div className="space-y-3 text-2xl font-semibold text-white">
              {companyLinks.map((link) => (
                <Link
                  key={link.href}
                  href={link.href}
                  className="block opacity-70 transition-opacity hover:opacity-100"
                >
                  {link.label}
                </Link>
              ))}
            </div>
            <div className="flex pt-6">
              <Link
                href="/dashboard"
                className="w-full rounded-2xl bg-white px-4 py-3 text-center text-lg font-semibold text-[#050015] shadow-[0_10px_30px_rgba(255,255,255,0.15)] transition-all hover:-translate-y-0.5"
              >
                Start for free
              </Link>
            </div>
          </div>
        </div>

        <div className="mt-16 flex flex-col gap-4 border-t border-white/10 pt-12 text-white/40 lg:flex-row lg:items-center lg:justify-between">
          <p className="text-5xl font-black uppercase tracking-tight text-white/70 lg:text-7xl">
            FinanceSum
          </p>
          <p className="text-xs uppercase tracking-[0.4em]">
            © {new Date().getFullYear()} FinanceSum. All rights reserved.
          </p>
        </div>
      </div>
    </section>
  );
}
