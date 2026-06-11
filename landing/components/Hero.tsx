"use client";

import { motion, useReducedMotion } from "framer-motion";
import dynamic from "next/dynamic";
import MagneticButton from "./MagneticButton";
import SplitText from "./SplitText";
import { APP_URL } from "@/lib/links";

const ParticleOcean = dynamic(() => import("./ParticleOcean"), { ssr: false });

const fade = (delay: number) => ({
  initial: { y: 26, opacity: 0 },
  animate: { y: 0, opacity: 1 },
  transition: { duration: 0.8, ease: [0.22, 1, 0.36, 1] as const, delay },
});

export default function Hero() {
  const reduced = useReducedMotion();

  return (
    <header id="top" className="relative flex min-h-[94vh] flex-col justify-center overflow-hidden">
      {!reduced && <ParticleOcean />}

      {/* ambient glows */}
      <div aria-hidden className="pointer-events-none absolute -left-40 -top-56 h-[640px] w-[640px]
        rounded-full bg-[radial-gradient(circle,#0E3A66_0%,transparent_70%)] opacity-60 blur-3xl" />
      <div aria-hidden className="pointer-events-none absolute -right-40 top-16 h-[520px] w-[520px]
        rounded-full bg-[radial-gradient(circle,rgba(217,162,27,0.20)_0%,transparent_70%)] blur-3xl" />

      {/* scrim so the copy owns its zone */}
      <div aria-hidden className="pointer-events-none absolute inset-x-[-30%] inset-y-[-6%]
        bg-[radial-gradient(ellipse_60%_52%_at_50%_46%,rgba(5,15,28,0.92)_0%,rgba(5,15,28,0.55)_55%,transparent_78%)]" />

      <div className="relative z-10 mx-auto max-w-4xl px-6 pt-24 text-center">
        <motion.a
          href="#access"
          {...fade(0)}
          className="mb-7 inline-flex items-center gap-2 rounded-full border border-gold-500/30
                     bg-gold-500/10 px-4 py-1.5 text-xs font-semibold text-gold-300
                     transition-colors hover:bg-gold-500/[0.18]"
        >
          <span className="h-1.5 w-1.5 rounded-full bg-gold-400 shadow-[0_0_8px_#DBAF34]" />
          Trusted with the books of 125+ Canadian businesses
        </motion.a>

        <h1 className="mb-6 text-[clamp(42px,7.2vw,84px)] font-extrabold leading-[1.04]
                       tracking-tight text-white [text-shadow:0_2px_36px_rgba(2,8,16,0.6)]">
          <SplitText text="Books that close" accent="themselves." />
        </h1>

        <motion.p
          {...fade(0.55)}
          className="mx-auto mb-9 max-w-xl text-[clamp(15px,2vw,19px)] leading-relaxed
                     text-mist/60 [text-shadow:0_1px_18px_rgba(2,8,16,0.7)]"
        >
          AcumenAI is the AI accounting OS for Canadian firms. Bank statements flow in from
          Gmail; reviewed, CRA-defensible Sage&nbsp;50 entries flow out — with you approving
          every judgment call.
        </motion.p>

        <motion.div {...fade(0.75)} className="mb-4 flex flex-wrap items-center justify-center gap-3">
          <MagneticButton href={APP_URL} variant="gold">
            ▶&nbsp; Watch the tour
          </MagneticButton>
          <MagneticButton href={APP_URL} variant="ghost">
            Explore the live demo →
          </MagneticButton>
        </motion.div>

        <motion.a
          {...fade(0.9)}
          href={APP_URL}
          className="text-[13px] font-medium text-gold-400 transition-colors hover:text-gold-300"
        >
          Already have access? Sign in →
        </motion.a>
      </div>

      {/* scroll cue */}
      <motion.a
        href="#how"
        aria-label="Scroll to how it works"
        initial={{ opacity: 0 }}
        animate={{ opacity: 1 }}
        transition={{ delay: 1.6, duration: 0.8 }}
        className="group absolute bottom-6 left-1/2 z-10 flex -translate-x-1/2 flex-col
                   items-center gap-2 font-mono text-[10px] uppercase tracking-[0.2em]
                   text-mist/35 transition-colors hover:text-gold-400"
      >
        Scroll
        <span className="relative h-9 w-px overflow-hidden bg-white/10">
          {!reduced && (
            <motion.span
              className="absolute left-0 top-0 h-full w-full bg-gold-400"
              animate={{ y: ["-100%", "100%"] }}
              transition={{ duration: 1.8, repeat: Infinity, ease: [0.22, 1, 0.36, 1] }}
            />
          )}
        </span>
      </motion.a>
    </header>
  );
}
