/** Where the landing hands off. Override per-environment on Vercel. */
export const APP_URL =
  process.env.NEXT_PUBLIC_APP_URL ?? "https://acumenai-api-lscziarcxa-pd.a.run.app";

/** The dashboard API (signup endpoint lives here). */
export const API_BASE =
  process.env.NEXT_PUBLIC_ACUMEN_API_BASE ?? APP_URL;

export const BOOK_DEMO_URL = "https://www.orchelix.com/book";
