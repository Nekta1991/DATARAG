import type { Metadata } from "next";
import { Caprasimo, IBM_Plex_Mono, Noto_Sans_JP, Zen_Maru_Gothic } from "next/font/google";
import localFont from "next/font/local";
import "./globals.css";

// Organic pairs Caprasimo headings with Figtree body; neither has Japanese
// glyphs, so Japanese headings use Zen Maru Gothic and Japanese body Noto Sans JP.
// Caprasimo is kept for the Latin wordmark only.
const caprasimo = Caprasimo({ variable: "--nf-caprasimo", weight: "400", subsets: ["latin"] });
// Figtree ships as a local file: on Vercel, Turbopack failed to resolve the
// next/font/google file URLs for Figtree only ("queries have exactly one entry").
// Same Google Fonts file (variable, latin, 400-700, OFL), so rendering is unchanged.
const figtree = localFont({ src: "./fonts/Figtree-latin.woff2", variable: "--nf-figtree", weight: "400 700", display: "swap" });
const zenMaru = Zen_Maru_Gothic({ variable: "--nf-zenmaru", weight: ["500", "700"], subsets: ["latin"], preload: false });
const noto = Noto_Sans_JP({ variable: "--nf-noto", weight: ["400", "500", "700"], subsets: ["latin"], preload: false });
const plex = IBM_Plex_Mono({ variable: "--nf-plex", weight: ["400", "500", "600"], subsets: ["latin"] });

export const metadata: Metadata = {
  title: "DATARAG query console",
  description: "非公式デモ — 補助金公式文書の RAG コンソール",
};

export default function RootLayout({ children }: LayoutProps<"/">) {
  const fonts = [caprasimo, figtree, zenMaru, noto, plex].map((f) => f.variable).join(" ");
  return (
    <html lang="ja" className={fonts}>
      <body>{children}</body>
    </html>
  );
}
