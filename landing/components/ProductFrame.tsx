"use client";

/**
 * The product showcase: a CSS-built browser frame that tilts up from 14° as
 * it scrolls into view (GSAP ScrollTrigger), follows the pointer in 3D, and
 * quietly "works" — rows flash as the AI categorizes them.
 */
import { gsap } from "gsap";
import { ScrollTrigger } from "gsap/ScrollTrigger";
import { useEffect, useRef, useState } from "react";

const ROWS = [
  { d: "01-08", desc: "Online Bill Payment — HYDRO QUEBEC", gl: "5790", amt: "293.90", conf: "0.95 auto", hi: true },
  { d: "01-08", desc: "INTERAC e-Transfer Received", gl: "4020", amt: "682.48", conf: "0.95 auto", hi: true },
  { d: "01-12", desc: "Pre-Authorized Payment — BENEVA", gl: "5688", amt: "181.62", conf: "0.92 auto", hi: true },
  { d: "01-15", desc: "CHQ#363 — City of Toronto", gl: "5800", amt: "1,905.00", conf: "review", hi: false },
];

const KPIS = [
  { l: "Active Clients", v: "125", d: "+3 this month" },
  { l: "Auto-categorized", v: "84%", d: "↑ learning weekly" },
  { l: "Pending review", v: "17", d: "8 min est." },
  { l: "Posted to Sage 50", v: "395", d: "one click" },
];

export default function ProductFrame() {
  const wrap = useRef<HTMLDivElement>(null);
  const frame = useRef<HTMLDivElement>(null);
  const [flash, setFlash] = useState(-1);
  const [reviewDone, setReviewDone] = useState(false);

  useEffect(() => {
    const reduced = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    if (reduced) return;

    gsap.registerPlugin(ScrollTrigger);
    const ctx = gsap.context(() => {
      gsap.fromTo(
        frame.current,
        { rotateX: 14, y: 90, autoAlpha: 0, transformPerspective: 1200 },
        {
          rotateX: 0, y: 0, autoAlpha: 1, ease: "none",
          scrollTrigger: { trigger: wrap.current, start: "top 92%", end: "top 38%", scrub: 0.6 },
        },
      );
    }, wrap);

    // pointer parallax (desktop)
    const el = wrap.current!;
    const fr = frame.current!;
    const canHover = window.matchMedia("(hover:hover)").matches;
    const onMove = (e: MouseEvent) => {
      const r = el.getBoundingClientRect();
      gsap.to(fr, {
        rotateY: ((e.clientX - r.left) / r.width - 0.5) * 5,
        rotateX: -((e.clientY - r.top) / r.height - 0.5) * 4,
        duration: 0.6, ease: "power2.out",
      });
    };
    const onLeave = () => gsap.to(fr, { rotateX: 0, rotateY: 0, duration: 0.8, ease: "power3.out" });
    if (canHover) { el.addEventListener("mousemove", onMove); el.addEventListener("mouseleave", onLeave); }

    // the mock works: rows flash, the review pill resolves
    let i = 0;
    const loop = setInterval(() => {
      if (document.hidden) return;
      setFlash(i % ROWS.length);
      if (i % ROWS.length === ROWS.length - 1) setReviewDone((v) => !v);
      i++;
    }, 2600);

    return () => {
      ctx.revert();
      clearInterval(loop);
      if (canHover) { el.removeEventListener("mousemove", onMove); el.removeEventListener("mouseleave", onLeave); }
    };
  }, []);

  return (
    <div ref={wrap} className="relative z-10 mx-auto mt-10 max-w-[980px] px-6 [perspective:1200px]">
      <div
        ref={frame}
        className="overflow-hidden rounded-2xl border border-white/10 bg-[#FBFBFC] text-left
                   shadow-[0_40px_120px_rgba(0,0,0,0.55),0_0_80px_rgba(217,162,27,0.07)]
                   [transform-style:preserve-3d] will-change-transform"
      >
        <div className="flex items-center gap-2 bg-[#0A1F36] px-4 py-3">
          <i className="block h-[11px] w-[11px] rounded-full bg-[#FF5F57]" />
          <i className="block h-[11px] w-[11px] rounded-full bg-[#FEBC2E]" />
          <i className="block h-[11px] w-[11px] rounded-full bg-[#28C840]" />
          <span className="ml-3 max-w-[380px] flex-1 rounded-md bg-white/[0.08] px-3 py-1.5
                           font-mono text-[11px] text-mist/45">
            app.acumenai.ca / review-entries
          </span>
        </div>

        <div className="grid gap-3.5 p-5">
          <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
            {KPIS.map((k) => (
              <div key={k.l} className="rounded-xl border border-[#E4E8EE] bg-white px-4 py-3.5
                                        transition-transform duration-200 hover:-translate-y-0.5">
                <div className="mb-2 text-[10px] font-semibold uppercase tracking-wider text-[#6D7F95]">{k.l}</div>
                <div className="text-[22px] font-bold text-[#0A2540]">{k.v}</div>
                <div className="mt-1.5 text-[10px] font-medium text-[#0F9A6C]">{k.d}</div>
              </div>
            ))}
          </div>

          <div className="overflow-hidden rounded-xl border border-[#E4E8EE] bg-white">
            <div className="grid grid-cols-[64px_1fr_80px] items-center gap-2.5 bg-[#F4F6F9] px-4
                            py-2.5 text-[10px] font-semibold uppercase tracking-wide text-[#6D7F95]
                            md:grid-cols-[76px_1fr_90px_84px_92px]">
              <span>Date</span><span>Description</span><span className="hidden md:block">GL</span>
              <span className="hidden md:block">Amount</span><span>Confidence</span>
            </div>
            {ROWS.map((r, i) => {
              const isReview = !r.hi && !reviewDone;
              return (
                <div
                  key={i}
                  className={`grid grid-cols-[64px_1fr_80px] items-center gap-2.5 border-t
                              border-[#EEF1F5] px-4 py-2.5 text-xs text-[#1B355A]
                              transition-colors duration-700 md:grid-cols-[76px_1fr_90px_84px_92px]
                              ${flash === i ? "bg-gold-500/15" : "bg-transparent"}`}
                >
                  <span className="font-mono text-[11px] text-[#3F5570]">{r.d}</span>
                  <span className="truncate">{r.desc}</span>
                  <span className="hidden font-mono text-[11px] text-[#3F5570] md:block">{r.gl}</span>
                  <span className="hidden font-mono text-[11px] text-[#3F5570] md:block">{r.amt}</span>
                  <span>
                    <span
                      className={`inline-block rounded-full px-2.5 py-1 font-mono text-[10px] font-semibold
                                  transition-all duration-300
                                  ${isReview ? "bg-[#FBF3DD] text-[#8F5E16]" : "bg-[#EAFAF4] text-[#0F9A6C]"}`}
                    >
                      {r.hi ? r.conf : isReview ? "review" : "0.91 approved"}
                    </span>
                  </span>
                </div>
              );
            })}
          </div>
        </div>
      </div>
    </div>
  );
}
