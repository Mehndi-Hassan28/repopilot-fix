import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "RepoPilot Fix — Autonomous Intelligence Platform",
  description: "Dark luxury developer intelligence platform for repository intelligence, autonomous patch review, and Docker sandbox verification.",
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="en" className="dark h-full bg-[#050505]">
      <body className="h-full bg-[#050505] text-[#f5f5f5] antialiased selection:bg-[#bfff00]/20 selection:text-[#bfff00]">
        <a
          href="#main-content"
          className="sr-only focus:not-sr-only focus:fixed focus:top-3 focus:left-3 focus:z-50 focus:px-4 focus:py-2 focus:bg-[#BFFF00] focus:text-black focus:font-bold focus:rounded-lg focus:shadow-2xl focus:outline-none"
        >
          Skip to main content
        </a>
        {children}
      </body>
    </html>
  );
}
