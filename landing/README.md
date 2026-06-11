# AcumenAI — Cinematic Landing (Next.js 15)

The public marketing front door for AcumenAI: React Three Fiber particle ocean,
Framer Motion split-text and micro-interactions, GSAP ScrollTrigger product-frame
choreography. Dark by default, reduced-motion safe, mobile-first.

## Run locally

```bash
cd landing
npm install
npm run dev          # http://localhost:3000
```

## Environment

| Var | Default | Purpose |
|---|---|---|
| `NEXT_PUBLIC_APP_URL` | the Cloud Run app URL | Where "Try the demo", "Sign in" and agent cards hand off |
| `NEXT_PUBLIC_ACUMEN_API_BASE` | same as APP_URL | Host of the public `/api/signup` endpoint |

## Deploy to Vercel

```bash
npm i -g vercel
cd landing
vercel              # link the project (first run)
vercel --prod
```

Or via the dashboard: **vercel.com → Add New → Project → import the repo**,
set Root Directory to `landing/`, add the two env vars above, deploy.

### After deploy — two one-line follow-ups

1. **CORS**: the signup endpoint only accepts the origins in the API's
   `CORS_ORIGIN` env var. Add your Vercel domain:
   ```powershell
   .\scripts\deploy_dashboard.ps1 -CorsOrigin "https://orchelix.com,https://www.orchelix.com,https://<your-landing>.vercel.app"
   ```
2. **orchelix.com's "Go to dashboard" button** (separate repo) should point at
   this landing's URL — that is what makes every visitor enter through the
   front door.

## Structure

```
app/layout.tsx            fonts (Montserrat/Fraunces/JetBrains), metadata, dark mode
app/page.tsx              section assembly
components/ParticleOcean  R3F: 6k round glow particles, low horizon, pointer parallax
components/Hero           split-text headline, magnetic CTAs, scroll cue
components/MagneticButton magnetic hover + click particle burst
components/ProductFrame   GSAP scroll tilt + pointer 3D + live categorization loop
components/Sections       marquee, count-up trust bar, how-it-works, agents grid
components/EarlyAccess    signup form → POST /api/signup (BigQuery leads)
components/Footer         watermark + functional links
```

Performance notes: WebGL only mounts client-side (`dynamic`, `ssr:false`) and not
at all under `prefers-reduced-motion`; DPR capped at 1.5; all animation easing is
`cubic-bezier(0.22,1,0.36,1)` for the expensive feel.
