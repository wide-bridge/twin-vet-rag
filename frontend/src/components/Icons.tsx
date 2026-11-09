// Small inline SVG icon set (no icon font download). Decorative unless given a label.
import type { SVGProps } from "react";

type P = SVGProps<SVGSVGElement> & { small?: boolean };
const base = (small?: boolean): SVGProps<SVGSVGElement> => ({
  className: small ? "icon-sm" : "icon",
  viewBox: "0 0 24 24",
  fill: "none",
  stroke: "currentColor",
  strokeWidth: 2,
  strokeLinecap: "round",
  strokeLinejoin: "round",
  "aria-hidden": true,
  focusable: false,
});

export const IconWarning = ({ small, ...p }: P) => (
  <svg {...base(small)} {...p}><path d="M10.3 3.9 1.8 18a2 2 0 0 0 1.7 3h17a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0z" /><path d="M12 9v4M12 17h.01" /></svg>
);
export const IconInfo = ({ small, ...p }: P) => (
  <svg {...base(small)} {...p}><circle cx="12" cy="12" r="10" /><path d="M12 16v-4M12 8h.01" /></svg>
);
export const IconQuestion = ({ small, ...p }: P) => (
  <svg {...base(small)} {...p}><circle cx="12" cy="12" r="10" /><path d="M9.1 9a3 3 0 0 1 5.8 1c0 2-3 3-3 3M12 17h.01" /></svg>
);
export const IconSearchOff = ({ small, ...p }: P) => (
  <svg {...base(small)} {...p}><circle cx="11" cy="11" r="7" /><path d="m21 21-4.3-4.3M8.5 8.5l5 5M13.5 8.5l-5 5" /></svg>
);
export const IconError = ({ small, ...p }: P) => (
  <svg {...base(small)} {...p}><circle cx="12" cy="12" r="10" /><path d="M12 8v4M12 16h.01" /></svg>
);
export const IconSpark = ({ small, ...p }: P) => (
  <svg {...base(small)} {...p}><path d="M12 3v3M12 18v3M3 12h3M18 12h3M5.6 5.6l2.1 2.1M16.3 16.3l2.1 2.1M5.6 18.4l2.1-2.1M16.3 7.7l2.1-2.1" /></svg>
);
export const IconDoc = ({ small, ...p }: P) => (
  <svg {...base(small)} {...p}><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" /><path d="M14 2v6h6M8 13h8M8 17h5" /></svg>
);
export const IconBook = ({ small, ...p }: P) => (
  <svg {...base(small)} {...p}><path d="M2 4h7a3 3 0 0 1 3 3v13a2 2 0 0 0-2-2H2zM22 4h-7a3 3 0 0 0-3 3v13a2 2 0 0 1 2-2h8z" /></svg>
);
export const IconChat = ({ small, ...p }: P) => (
  <svg {...base(small)} {...p}><path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z" /></svg>
);
export const IconThumbUp = ({ small, ...p }: P) => (
  <svg {...base(small)} {...p}><path d="M7 10v11H3V10zM7 10l4-8a3 3 0 0 1 3 3v4h5.5a2 2 0 0 1 2 2.3l-1.4 8A2 2 0 0 1 18 21H7" /></svg>
);
export const IconThumbDown = ({ small, ...p }: P) => (
  <svg {...base(small)} {...p}><path d="M17 14V3h4v11zM17 14l-4 8a3 3 0 0 1-3-3v-4H4.5a2 2 0 0 1-2-2.3l1.4-8A2 2 0 0 1 6 3h11" /></svg>
);
export const IconClose = ({ small, ...p }: P) => (
  <svg {...base(small)} {...p}><path d="M18 6 6 18M6 6l12 12" /></svg>
);
export const IconBack = ({ small, ...p }: P) => (
  <svg {...base(small)} {...p}><path d="M15 18l-6-6 6-6" /></svg>
);
export const IconMore = ({ small, ...p }: P) => (
  <svg {...base(small)} {...p}><circle cx="12" cy="5" r="1" /><circle cx="12" cy="12" r="1" /><circle cx="12" cy="19" r="1" /></svg>
);
export const IconChevron = ({ small, open, ...p }: P & { open?: boolean }) => (
  <svg {...base(small)} {...p}><path d={open ? "M6 9l6 6 6-6" : "M9 18l6-6-6-6"} /></svg>
);
export const IconPaw = ({ small, ...p }: P) => (
  <svg {...base(small)} {...p} fill="currentColor" stroke="none"><circle cx="5.5" cy="10" r="2.2" /><circle cx="9.5" cy="5.5" r="2.2" /><circle cx="14.5" cy="5.5" r="2.2" /><circle cx="18.5" cy="10" r="2.2" /><path d="M12 11c-3 0-6 4-6 6.5 0 1.7 1.3 2.5 3 2.5 1.2 0 2-.6 3-.6s1.8.6 3 .6c1.7 0 3-.8 3-2.5C18 15 15 11 12 11z" /></svg>
);
export const IconStethoscope = ({ small, ...p }: P) => (
  <svg {...base(small)} {...p}><path d="M5 3v5a5 5 0 0 0 10 0V3M10 13v2a5 5 0 0 0 10 0v-2" /><circle cx="20" cy="11" r="2" /></svg>
);
export const IconLock = ({ small, ...p }: P) => (
  <svg {...base(small)} {...p}><rect x="4" y="11" width="16" height="10" rx="2" /><path d="M8 11V7a4 4 0 0 1 8 0v4" /></svg>
);
export const IconCheck = ({ small, ...p }: P) => (
  <svg {...base(small)} {...p}><path d="M20 6 9 17l-5-5" /></svg>
);
export const IconSend = ({ small, ...p }: P) => (
  <svg {...base(small)} {...p}><path d="M12 19V5M5 12l7-7 7 7" /></svg>
);
export const IconBulb = ({ small, ...p }: P) => (
  <svg {...base(small)} {...p}><path d="M9 18h6M10 22h4M12 2a7 7 0 0 0-4 12.7V17h8v-2.3A7 7 0 0 0 12 2z" /></svg>
);
