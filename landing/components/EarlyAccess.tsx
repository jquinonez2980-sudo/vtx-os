"use client";

/**
 * Early-access capture — posts to the AcumenAI API's public /api/signup
 * (honeypot + rate-limited server-side; leads land in BigQuery).
 */
import { AnimatePresence, motion } from "framer-motion";
import { useState } from "react";
import { API_BASE, APP_URL } from "@/lib/links";

const EASE = [0.22, 1, 0.36, 1] as const;

export default function EarlyAccess() {
  const [state, setState] = useState<"idle" | "busy" | "done">("idle");
  const [error, setError] = useState("");

  async function submit(e: React.FormEvent<HTMLFormElement>) {
    e.preventDefault();
    const f = new FormData(e.currentTarget);
    const email = String(f.get("email") ?? "").trim();
    if (!email) return;
    setState("busy");
    setError("");
    try {
      const qs = new URLSearchParams({
        email,
        name: String(f.get("name") ?? "").trim(),
        firm: String(f.get("firm") ?? "").trim(),
        clients: String(f.get("clients") ?? ""),
        website: String(f.get("website") ?? ""), // honeypot
      });
      const resp = await fetch(`${API_BASE}/api/signup?${qs}`, { method: "POST" });
      const data = await resp.json().catch(() => ({}));
      if (!resp.ok) throw new Error(data.detail ?? `HTTP ${resp.status}`);
      setState("done");
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
      setState("idle");
    }
  }

  const input =
    "w-full rounded-xl border border-white/15 bg-ink-950/65 px-4 py-3.5 text-sm text-white " +
    "placeholder:text-mist/35 transition-all duration-150 focus:border-gold-400 " +
    "focus:shadow-[0_0_0_3px_rgba(217,162,27,0.18)] focus:outline-none";

  return (
    <section id="access" className="relative z-10 mx-auto mt-24 max-w-2xl scroll-mt-24 px-6">
      <motion.div
        initial={{ y: 36, opacity: 0, scale: 0.985 }}
        whileInView={{ y: 0, opacity: 1, scale: 1 }}
        viewport={{ once: true, margin: "-10% 0px" }}
        transition={{ duration: 0.9, ease: EASE }}
        className="rounded-3xl border border-gold-500/[0.28] p-8 text-center sm:p-11
                   [background:linear-gradient(150deg,rgba(217,162,27,0.10),rgba(255,255,255,0.03)_45%)]"
      >
        <div className="mb-3.5 text-xs font-bold uppercase tracking-[0.14em] text-gold-400">Early access</div>
        <h2 className="mb-3 text-[clamp(22px,3.4vw,32px)] font-extrabold tracking-tight text-white">
          Put your firm&apos;s books on autopilot
        </h2>
        <p className="mx-auto max-w-md text-[15px] leading-relaxed text-mist/55">
          We onboard a small number of Canadian firms each month. Tell us about yours
          and we&apos;ll reach out with next steps.
        </p>

        <AnimatePresence mode="wait">
          {state !== "done" ? (
            <motion.form
              key="form"
              exit={{ opacity: 0, y: -10 }}
              onSubmit={submit}
              className="mt-7 grid grid-cols-1 gap-3 text-left sm:grid-cols-2"
            >
              <input className={input} name="name" placeholder="Your name" autoComplete="name" />
              <input className={input} name="firm" placeholder="Firm / company" autoComplete="organization" />
              <input className={`${input} sm:col-span-2`} name="email" type="email" required
                     placeholder="Work email *" autoComplete="email" />
              <select className={`${input} cursor-pointer appearance-none sm:col-span-2`}
                      name="clients" defaultValue="">
                <option value="">How many sets of books? (optional)</option>
                <option>1–10</option><option>11–50</option><option>51–150</option><option>150+</option>
              </select>
              {/* honeypot */}
              <input className="absolute -left-[9999px]" name="website" tabIndex={-1}
                     autoComplete="off" aria-hidden="true" />
              <motion.button
                type="submit"
                disabled={state === "busy"}
                whileTap={{ scale: 0.97 }}
                className="btn-sheen rounded-xl bg-gold-500 px-7 py-4 text-base font-bold
                           text-[#1A1206] shadow-[0_4px_24px_rgba(217,162,27,0.35)]
                           transition-all duration-200 hover:bg-gold-400
                           hover:shadow-[0_6px_34px_rgba(217,162,27,0.5)]
                           disabled:opacity-60 sm:col-span-2"
              >
                {state === "busy" ? "Sending…" : "Request early access"}
              </motion.button>
              {error && <p className="text-xs text-red-400 sm:col-span-2">{error}</p>}
            </motion.form>
          ) : (
            <motion.div
              key="done"
              initial={{ opacity: 0, scale: 0.92 }}
              animate={{ opacity: 1, scale: 1 }}
              transition={{ duration: 0.6, ease: EASE }}
              className="pt-8"
            >
              <motion.div
                initial={{ scale: 0 }}
                animate={{ scale: 1 }}
                transition={{ type: "spring", stiffness: 260, damping: 16, delay: 0.1 }}
                className="mx-auto mb-4 flex h-14 w-14 items-center justify-center rounded-full
                           border border-emerald-500/50 bg-emerald-500/15 text-2xl text-emerald-400"
              >
                ✓
              </motion.div>
              <h3 className="mb-2 text-lg font-bold text-white">You&apos;re on the list</h3>
              <p className="text-sm leading-relaxed text-mist/60">
                We&apos;ll be in touch shortly. Meanwhile,{" "}
                <a href={APP_URL} className="text-gold-400 hover:text-gold-300">
                  take the interactive demo →
                </a>
              </p>
            </motion.div>
          )}
        </AnimatePresence>

        <div className="mt-4 text-[11.5px] text-mist/40">
          No spam, no card. Your email is used only to arrange onboarding.
        </div>
      </motion.div>
    </section>
  );
}
