"use client";

/**
 * Word-level staggered reveal: rise + 3D unfold + fade, expensive easing.
 * Respects reduced motion (renders statically).
 */
import { motion, useReducedMotion, type Variants } from "framer-motion";

const container: Variants = {
  hidden: {},
  show: { transition: { staggerChildren: 0.07, delayChildren: 0.15 } },
};

const word: Variants = {
  hidden: { y: "110%", rotateX: -55, opacity: 0 },
  show: {
    y: "0%",
    rotateX: 0,
    opacity: 1,
    transition: { duration: 0.9, ease: [0.22, 1, 0.36, 1] },
  },
};

export default function SplitText({
  text,
  accent,
  className = "",
}: {
  text: string;          // plain words
  accent?: string;       // trailing serif-italic accent word(s)
  className?: string;
}) {
  const reduced = useReducedMotion();
  const words = text.split(" ").filter(Boolean);

  if (reduced) {
    return (
      <span className={className}>
        {text}{" "}
        {accent && <em className="font-serif italic text-gold-400 not-italic:hidden">{accent}</em>}
      </span>
    );
  }

  return (
    <motion.span
      variants={container}
      initial="hidden"
      animate="show"
      className={`inline-block [perspective:800px] ${className}`}
    >
      {words.map((w, i) => (
        <span key={i} className="inline-block overflow-hidden pb-[0.12em] align-bottom">
          <motion.span variants={word} className="inline-block origin-bottom will-change-transform">
            {w}
          </motion.span>
          <span className="inline-block">&nbsp;</span>
        </span>
      ))}
      {accent && (
        <span className="inline-block overflow-hidden pb-[0.12em] align-bottom">
          <motion.span
            variants={word}
            className="inline-block origin-bottom font-serif italic text-gold-400
                       [text-shadow:0_0_44px_rgba(217,162,27,0.35)] will-change-transform"
          >
            {accent}
          </motion.span>
        </span>
      )}
    </motion.span>
  );
}
