import Link from "next/link";

export default function NotFound() {
  return (
    <div className="flex min-h-screen flex-col items-center justify-center p-6 text-center">
      <h2 className="text-2xl font-bold">Page Not Found</h2>
      <p className="mt-2 text-sm text-[var(--muted)]">Could not find requested resource</p>
      <Link href="/" className="mt-4 rounded-xl bg-[var(--accent)] px-4 py-2 text-xs font-semibold text-[#092018]">
        Return Home
      </Link>
    </div>
  );
}
