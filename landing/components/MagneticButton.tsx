"use client";

/**
 * Magnetic CTA: leans toward the cursor (desktop), springs back on leave,
 * compresses on press, and fires a radial particle burst on click.
 */
import { motion, useMotionValue, useReducedMotion, useSpring } from "framer-motion";
import { useCallback, useRef, useState } from "react";

type Burst = { id: number; x: number; y: number };

const PARTICLES = 14;

export default function MagneticButton({
  children,
  className = "",
  onClick,
  href,
  variant = "gold",
}: {
  children: React.ReactNode;
  className?: string;
  onClick?: () => void;
  href?: string;
  variant?: "gold" | "ghost";
}) {
  const reduced = useReducedMotion();
  const ref = useRef<HTMLDivElement>(null);
  const mx = useMotionValue(0);
  const my = useMotionValue(0);
  const x = useSpring(mx, { stiffness: 320, damping: 22 });
  const y = useSpring(my, { stiffness: 320, damping: 22 });
  const [bursts, setBursts] = useState<Burst[]>([]);

  const onMove = useCallback(
    (e: React.PointerEvent) => {
      if (reduced || e.pointerType !== "mouse") return;
      const r = ref.current!.getBoundingClientRect();
      mx.set((e.clientX - r.left - r.width / 2) * 0.18);
      my.set((e.clientY - r.top - r.height / 2) * 0.3);
    },
    [mx, my, reduced],
  );

  const onLeave = useCallback(() => {
    mx.set(0);
    my.set(0);
  }, [mx, my]);

  const fire = useCallback(
    (e: React.MouseEvent) => {
      if (!reduced) {
        const r = ref.current!.getBoundingClientRect();
        setBursts((b) => [
          ...b.slice(-3),
          { id: Date.now(), x: e.clientX - r.left, y: e.clientY - r.top },
        ]);
      }
      onClick?.();
      if (href) window.location.href = href;
    },
    [href, onClick, reduced],
  );

  const base =
    variant === "gold"
      ? "btn-sheen bg-gold-500 text-[#1A1206] font-bold shadow-[0_4px_24px_rgba(217,162,27,0.35)] hover:bg-gold-400 hover:shadow-[0_6px_34px_rgba(217,162,27,0.5)]"
      : "border border-white/15 text-mist/80 hover:bg-white/[0.07] hover:text-white";

  return (
    <motion.div ref={ref} style={{ x, y }} className="relative inline-block">
      <motion.button
        type="button"
        whileTap={reduced ? undefined : { scale: 0.96 }}
        onPointerMove={onMove}
        onPointerLeave={onLeave}
        onClick={fire}
        className={`relative inline-flex items-center justify-center gap-2 rounded-xl
                    px-7 py-4 text-base transition-colors duration-200 ${base} ${className}`}
      >
        {children}
      </motion.button>

      {bursts.map((b) => (
        <span key={b.id} className="pointer-events-none absolute" style={{ left: b.x, top: b.y }}>
          {Array.from({ length: PARTICLES }).map((_, i) => {
            const a = (i / PARTICLES) * Math.PI * 2;
            const d = 28 + (i % 3) * 14;
            return (
              <motion.span
                key={i}
                initial={{ x: 0, y: 0, opacity: 1, scale: 1 }}
                animate={{ x: Math.cos(a) * d, y: Math.sin(a) * d, opacity: 0, scale: 0.2 }}
                transition={{ duration: 0.55, ease: [0.22, 1, 0.36, 1] }}
                className="absolute h-1.5 w-1.5 rounded-full"
                style={{ background: i % 4 === 0 ? "#14B8A6" : "#E3BC54" }}
              />
            );
          })}
        </span>
      ))}
    </motion.div>
  );
}
