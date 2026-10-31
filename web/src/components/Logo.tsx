// GaltonKS brand: the layered-plates mark and the wordmark, drawn as SVG so they stay
// sharp at any size. Source artwork: web/public/brand/ (light and dark versions).
// Three plates, bottom to top: navy grid, pale grid, and the dotted plate that runs
// from navy on the left to gold on the right.

import { useId } from "react";

const NAVY = "#000815"; // logo navy, same as --color-navy
const PLATE = "M2 14 L28 2 L54 14 L28 26 Z"; // one isometric plate, 56 wide

/** The stacked-plates mark. `width` in px; height follows (56:44). */
export function LogoMark({ width = 44, className = "", onDark = false }: {
  width?: number; className?: string; onDark?: boolean;
}) {
  const id = useId().replace(/:/g, "");
  // Dot grid on the top plate, in plate coordinates (u, v from 0 to 1).
  const dots: [number, number][] = [];
  for (let i = 1; i <= 4; i++) for (let j = 1; j <= 4; j++) dots.push([i / 5, j / 5]);
  const at = (u: number, v: number): [number, number] =>
    // Corners: left (2,14), top (28,2), right (54,14), bottom (28,26).
    [2 + 26 * u + 26 * v, 14 - 12 * u + 12 * v];
  return (
    <svg viewBox="0 0 56 44" width={width} height={(width * 44) / 56} className={className}
         role="img" aria-label="GaltonKS">
      <defs>
        <linearGradient id={`${id}top`} x1="0" y1="0" x2="1" y2="0">
          <stop offset="0" stopColor={NAVY} />
          <stop offset="0.55" stopColor="#3a3424" />
          <stop offset="1" stopColor="#d9a62e" />
        </linearGradient>
        <linearGradient id={`${id}mid`} x1="0" y1="0" x2="1" y2="0">
          <stop offset="0" stopColor="#8f8a80" />
          <stop offset="1" stopColor="#efe2c4" />
        </linearGradient>
      </defs>
      {/* bottom: navy grid */}
      <g transform="translate(0 16)">
        {/* On the navy header the plate gets the icon's lighter fill and edge, or it vanishes. */}
        <path d={PLATE} fill={onDark ? "#1b2638" : NAVY} stroke={onDark ? "#6d7686" : "none"} strokeWidth="0.5" />
        <path d="M15 8 L41 20 M15 20 L41 8" stroke={onDark ? "#4a5466" : "#3b4658"} strokeWidth="0.6" />
      </g>
      {/* middle: pale grid */}
      <g transform="translate(0 8)">
        <path d={PLATE} fill={`url(#${id}mid)`} stroke="#fdfaf3" strokeWidth="0.5" />
        <path d="M15 8 L41 20 M15 20 L41 8" stroke="#fdfaf3" strokeWidth="0.5" opacity="0.8" />
      </g>
      {/* top: dotted plate, navy to gold */}
      <path d={PLATE} fill={`url(#${id}top)`} stroke="#e8b83a" strokeWidth="0.6" />
      {dots.map(([u, v]) => {
        const [x, y] = at(u, v);
        return <circle key={`${u}-${v}`} cx={x} cy={y} r="1.05" fill="#f2c14e" opacity={0.45 + 0.55 * u} />;
      })}
    </svg>
  );
}

/** Mark plus wordmark: GALTONKS over KNOWLEDGE SYSTEM, with KS in gold. `onDark` is
 *  the dark-icon version for the navy header; the default is the light (paper) version. */
export function Logo({ markWidth = 40, onDark = false }: { markWidth?: number; onDark?: boolean }) {
  return (
    <span className={`flex items-center gap-2.5 ${onDark ? "logo-on-dark" : ""}`}>
      <LogoMark width={markWidth} onDark={onDark} />
      <span className="leading-none">
        <span className="wordmark block text-[17px]">
          GALTON<span className="wordmark-ks">KS</span>
        </span>
        <span className="wordmark-sub mt-1 block">Knowledge System</span>
      </span>
    </span>
  );
}
