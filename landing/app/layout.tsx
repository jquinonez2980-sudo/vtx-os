import type { Metadata, Viewport } from "next";
import { Fraunces, JetBrains_Mono, Montserrat } from "next/font/google";
import "./globals.css";

const montserrat = Montserrat({
  subsets: ["latin"],
  weight: ["400", "500", "600", "700", "800"],
  variable: "--font-montserrat",
});
const fraunces = Fraunces({
  subsets: ["latin"],
  style: ["italic"],
  weight: ["400", "600"],
  variable: "--font-fraunces",
});
const jetbrains = JetBrains_Mono({
  subsets: ["latin"],
  weight: ["400", "500"],
  variable: "--font-jetbrains",
});

export const metadata: Metadata = {
  title: "AcumenAI — Books that close themselves | AI Accounting OS for Canadian firms",
  description:
    "AcumenAI by Orchelix turns emailed bank statements into reviewed, CRA-defensible Sage 50 entries. AI categorization, human-approved posting, full audit trail.",
  openGraph: {
    type: "website",
    siteName: "AcumenAI by Orchelix",
    title: "AcumenAI — Books that close themselves",
    description:
      "Bank statements in, reviewed Sage 50 entries out — with you approving every judgment call.",
  },
};

export const viewport: Viewport = {
  themeColor: "#050F1C",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html
      lang="en"
      className={`dark ${montserrat.variable} ${fraunces.variable} ${jetbrains.variable}`}
    >
      <body className="grain font-display">{children}</body>
    </html>
  );
}
