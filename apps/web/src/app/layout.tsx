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
        {children}
      </body>
    </html>
  );
}
