"use client";

import { motion } from "framer-motion";
import { APP_URL, BOOK_DEMO_URL } from "@/lib/links";

export default function Footer() {
  return (
    <footer className="relative z-10 mt-28 overflow-hidden border-t border-white/[0.07]
                       px-6 pb-12 pt-8 sm:px-12">
      <motion.div
        aria-hidden
        initial={{ y: "40%", opacity: 0 }}
        whileInView={{ y: 0, opacity: 1 }}
        viewport={{ once: true }}
        transition={{ duration: 1.1, ease: [0.22, 1, 0.36, 1] }}
        className="pointer-events-none absolute -bottom-9 -right-2 select-none whitespace-nowrap
                   text-[clamp(80px,14vw,160px)] font-extrabold leading-[0.8] tracking-tight
                   text-white/[0.025]"
      >
        AcumenAI
      </motion.div>

      <div className="relative flex flex-wrap items-center gap-5">
        <div className="text-xs text-mist/35">
          © 2026 Orchelix · AcumenAI — Accounting OS for Canadian SMBs
        </div>
        <nav className="ml-auto flex flex-wrap gap-5">
          {[
            { label: "orchelix.com", href: "https://www.orchelix.com", ext: true },
            { label: "Book a live demo", href: BOOK_DEMO_URL, ext: true },
            { label: "Interactive demo", href: APP_URL },
            { label: "Sign in", href: APP_URL },
          ].map((l) => (
            <a
              key={l.label}
              href={l.href}
              {...(l.ext ? { target: "_blank", rel: "noopener" } : {})}
              className="text-xs font-medium text-mist/55 transition-colors hover:text-white"
            >
              {l.label}
            </a>
          ))}
        </nav>
      </div>
    </footer>
  );
}
