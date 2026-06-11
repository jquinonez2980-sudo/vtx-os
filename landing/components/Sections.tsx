"use client";

/**
 * Marquee, trust metrics (count-up), How-it-works, and the Agents grid —
 * each section animates in with cinematic timing via whileInView.
 */
import { animate, motion, useInView, useReducedMotion } from "framer-motion";
import { useEffect, useRef } from "react";
import { APP_URL } from "@/lib/links";

const EASE = [0.22, 1, 0.36, 1] as const;

const sectionReveal = {
  initial: { y: 36, opacity: 0 },
  whileInView: { y: 0, opacity: 1 },
  viewport: { once: true, margin: "-12% 0px" },
  transition: { duration: 0.9, ease: EASE },
};

/* ── bank marquee ─────────────────────────────────────────── */
const BANKS = "RBC · TD · BMO · CIBC · Scotiabank · Desjardins · National Bank · Sage 50 · QuickBooks soon ·";

export function Marquee() {
  return (
    <div aria-hidden className="relative z-10 mt-14 overflow-hidden
      [mask-image:linear-gradient(to_right,transparent,#000_12%,#000_88%,transparent)]">
      <div className="marquee-track flex w-max gap-14 py-1.5">
        {[0, 1].map((k) => (
          <span key={k} className="whitespace-nowrap text-[13px] font-bold uppercase
                                   tracking-[0.22em] text-mist/25">
            {BANKS.split("·").map((b, i) => (
              <span key={i}>{b}<b className="font-bold text-gold-500/45"> · </b></span>
            ))}
          </span>
        ))}
      </div>
    </div>
  );
}

/* ── trust metrics with count-up ──────────────────────────── */
function Counter({ to, suffix = "" }: { to: number; suffix?: string }) {
  const ref = useRef<HTMLSpanElement>(null);
  const inView = useInView(ref, { once: true, margin: "-10% 0px" });
  const reduced = useReducedMotion();

  useEffect(() => {
    if (!inView || !ref.current) return;
    if (reduced) { ref.current.textContent = `${to}${suffix}`; return; }
    const ctrl = animate(0, to, {
      duration: 1.6,
      ease: "easeOut",
      onUpdate: (v) => { ref.current!.textContent = `${Math.round(v)}${suffix}`; },
    });
    return () => ctrl.stop();
  }, [inView, reduced, suffix, to]);

  return <span ref={ref}>0{suffix}</span>;
}

export function Trust() {
  const items = [
    { v: <Counter to={125} suffix="+" />, l: "Canadian businesses" },
    { v: <Counter to={7} />, l: "banks auto-parsed" },
    { v: <Counter to={100} suffix="%" />, l: "human-approved entries" },
    { v: "CRA", l: "defensible audit trail" },
  ];
  return (
    <motion.div {...sectionReveal}
      className="relative z-10 flex flex-wrap justify-center gap-10 px-6 pb-2 pt-16 sm:gap-16">
      {items.map((it, i) => (
        <div key={i} className="text-center">
          <b className="block text-[clamp(22px,3vw,32px)] font-extrabold text-white">{it.v}</b>
          <span className="text-[11px] font-medium uppercase tracking-[0.08em] text-mist/45">{it.l}</span>
        </div>
      ))}
    </motion.div>
  );
}

/* ── how it works ─────────────────────────────────────────── */
const STEPS = [
  {
    n: "01", t: "Statements arrive",
    p: "Clients email statements as usual. AcumenAI watches the inbox, recognizes PDFs and CSVs from RBC, TD, BMO, CIBC, Scotiabank, Desjardins and National Bank, and extracts every transaction — validated against the running balance.",
  },
  {
    n: "02", t: "You review the judgment calls",
    p: "Client-specific rules categorize the routine — typically 80%+. The rest queues for one-click review with suggested GL codes. Approve, correct, or reject; your decision is what posts. Nothing moves without it.",
  },
  {
    n: "03", t: "One click to Sage 50",
    p: "Approved entries post as balanced journal entries — duplicate-checked against the ledger, backed up first, every step written to an immutable audit log. QuickBooks Online support is on the roadmap.",
  },
];

export function HowItWorks() {
  return (
    <section id="how" className="relative z-10 mx-auto max-w-5xl scroll-mt-24 px-6 pt-24">
      <motion.div {...sectionReveal} className="text-center">
        <div className="mb-3.5 text-xs font-bold uppercase tracking-[0.14em] text-gold-400">How it works</div>
        <h2 className="mb-3.5 text-[clamp(26px,4vw,40px)] font-extrabold tracking-tight text-white">
          From inbox to posted ledger in three steps
        </h2>
        <p className="mx-auto mb-11 max-w-xl text-[15px] leading-relaxed text-mist/55">
          No exports, no re-keying, no mystery categorizations. Every transaction carries
          its confidence score and its audit trail.
        </p>
      </motion.div>

      <div className="grid gap-4 md:grid-cols-3">
        {STEPS.map((s, i) => (
          <motion.div
            key={s.n}
            initial={{ y: 36, opacity: 0 }}
            whileInView={{ y: 0, opacity: 1 }}
            viewport={{ once: true, margin: "-10% 0px" }}
            transition={{ duration: 0.8, ease: EASE, delay: i * 0.12 }}
            whileHover={{ y: -4 }}
            className="rounded-2xl border border-white/[0.08] bg-white/[0.035] p-7
                       transition-colors duration-200 hover:border-gold-500/35 hover:bg-white/[0.055]"
          >
            <div className="mb-4 font-mono text-[13px] font-extrabold text-gold-400">{s.n}</div>
            <h3 className="mb-2.5 text-lg font-bold text-white">{s.t}</h3>
            <p className="text-[13.5px] leading-relaxed text-mist/55">{s.p}</p>
          </motion.div>
        ))}
      </div>
    </section>
  );
}

/* ── agents grid (every card functional → app) ─────────────── */
const AGENTS = [
  { t: "Ingestion & OCR", p: "Statement parsing across 7 banks, three-tier OCR with balance-chain validation." },
  { t: "Bookkeeping", p: "Per-client rulesets that learn your chart of accounts and history." },
  { t: "HST & Compliance", p: "GST/HST tracking with CRA rules baked in, GST34-ready summaries." },
  { t: "Sage 50 Posting", p: "Balanced, deduplicated journal entries — backups before every batch." },
  { t: "Reconciliation", p: "Transaction matching with exception flagging against the GL." },
  { t: "Financial Statements", p: "Year-end worksheets and reports generated from reconciled data." },
  { t: "Tax Preparation", p: "T2-ready data, Cantax integration on the roadmap." },
  { t: "Orchestrator", p: "Pipeline state, handoffs, and human-in-the-loop approvals across all agents." },
];

export function Agents() {
  return (
    <section id="agents" className="relative z-10 mx-auto max-w-5xl scroll-mt-24 px-6 pt-24">
      <motion.div {...sectionReveal} className="text-center">
        <div className="mb-3.5 text-xs font-bold uppercase tracking-[0.14em] text-gold-400">Under the hood</div>
        <h2 className="mb-3.5 text-[clamp(26px,4vw,40px)] font-extrabold tracking-tight text-white">
          A team of specialist agents, one orchestrator
        </h2>
        <p className="mx-auto mb-11 max-w-xl text-[15px] leading-relaxed text-mist/55">
          Each stage of the close has a dedicated agent. Click any of them to step inside the product.
        </p>
      </motion.div>

      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-4">
        {AGENTS.map((a, i) => (
          <motion.a
            key={a.t}
            href={APP_URL}
            initial={{ y: 28, opacity: 0 }}
            whileInView={{ y: 0, opacity: 1 }}
            viewport={{ once: true, margin: "-8% 0px" }}
            transition={{ duration: 0.7, ease: EASE, delay: (i % 4) * 0.08 }}
            whileHover={{ y: -3 }}
            className="flex flex-col gap-2 rounded-2xl border border-white/[0.08] bg-white/[0.035]
                       p-[18px] text-left transition-colors duration-200
                       hover:border-gold-500/40 hover:bg-gold-500/[0.06]"
          >
            <b className="text-sm font-bold text-white">{a.t}</b>
            <span className="text-[11.5px] leading-relaxed text-mist/50">{a.p}</span>
            <span className="mt-0.5 text-[11px] font-semibold text-gold-400">See it live →</span>
          </motion.a>
        ))}
      </div>
    </section>
  );
}
