/** Brand mark · used in the sidebar header and AuthGate.
 *
 *  The mark is a stylised "search/lens + spark" — references retrieval
 *  + AI without being literal. SVG keeps it crisp at any size and lets
 *  the gradient adopt the current theme's --primary.
 */
export function BrandMark({
  size = 28,
  className,
}: {
  size?: number;
  className?: string;
}) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 32 32"
      fill="none"
      xmlns="http://www.w3.org/2000/svg"
      className={className}
      role="img"
      aria-label="self-rag"
    >
      <defs>
        <linearGradient id="brand-grad" x1="0" y1="0" x2="32" y2="32">
          <stop offset="0" stopColor="hsl(var(--primary))" />
          <stop offset="1" stopColor="hsl(244 78% 76%)" />
        </linearGradient>
      </defs>
      <rect width="32" height="32" rx="8" fill="url(#brand-grad)" />
      {/* concentric loop suggesting retrieval + iteration */}
      <path
        d="M16 8 a 8 8 0 1 1 -5.66 13.66"
        stroke="white"
        strokeWidth="2.2"
        strokeLinecap="round"
        fill="none"
        opacity="0.95"
      />
      {/* spark inside */}
      <circle cx="16" cy="16" r="2.6" fill="white" />
    </svg>
  );
}

export function BrandWordmark({ className }: { className?: string }) {
  return (
    <div className={"flex items-center gap-2 " + (className ?? "")}>
      <BrandMark size={24} />
      <span className="text-base font-semibold tracking-tight">
        self<span className="text-primary">·</span>rag
      </span>
    </div>
  );
}
