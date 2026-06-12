"use client";

import { motion, useScroll, useTransform } from "framer-motion";
import { APP_URL } from "@/lib/links";

const links = [
  { label: "How it works", href: "#how" },
  { label: "The agents", href: "#agents" },
  { label: "Early access", href: "#access" },
];

export default function Nav() {
  const { scrollY } = useScroll();
  const bg = useTransform(scrollY, [0, 80], ["rgba(5,15,28,0.45)", "rgba(5,15,28,0.88)"]);
  const border = useTransform(scrollY, [0, 80], ["rgba(255,255,255,0)", "rgba(255,255,255,0.08)"]);

  return (
    <motion.nav
      style={{ background: bg, borderColor: border }}
      className="fixed inset-x-0 top-0 z-40 flex items-center gap-7 border-b px-5 py-4
                 backdrop-blur-xl sm:px-10 lg:px-14"
      initial={{ y: -28, opacity: 0 }}
      animate={{ y: 0, opacity: 1 }}
      transition={{ duration: 0.7, ease: [0.22, 1, 0.36, 1], delay: 0.1 }}
    >
      <a href="#top">
        {/* eslint-disable-next-line @next/next/no-img-element */}
        <img src="/acumenai-logo.png" alt="AcumenAI" className="h-10 w-auto" />
      </a>

      <div className="ml-3 hidden gap-6 md:flex">
        {links.map((l) => (
          <a
            key={l.href}
            href={l.href}
            className="group relative text-[13px] font-medium text-mist/60 transition-colors hover:text-white"
          >
            {l.label}
            <span className="absolute -bottom-1 left-0 h-px w-0 bg-gold-400 transition-all
                             duration-300 group-hover:w-full" />
          </a>
        ))}
      </div>

      <div className="ml-auto flex items-center gap-2.5">
        <a
          href={APP_URL}
          className="rounded-xl border border-white/15 px-5 py-2.5 text-sm font-semibold
                     text-mist/80 transition-all duration-200 hover:bg-white/[0.07] hover:text-white"
        >
          Sign in
        </a>
        <a
          href={APP_URL}
          className="btn-sheen rounded-xl bg-gold-500 px-5 py-2.5 text-sm font-bold text-[#1A1206]
                     shadow-[0_4px_20px_rgba(217,162,27,0.35)] transition-all duration-200
                     hover:bg-gold-400 hover:shadow-[0_6px_28px_rgba(217,162,27,0.5)]"
        >
          Try the demo
        </a>
      </div>
    </motion.nav>
  );
}
