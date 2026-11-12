// Main menu [상담 | 케어 | 설정] (docs/05 §1, §9-2): a bottom bar on mobile/tablet, tabs in the header on PC.
import { createContext, useContext } from "react";

export type Tab = "consult" | "care" | "settings";
export const TABS: [Tab, string][] = [["consult", "상담"], ["care", "케어"], ["settings", "설정"]];

export const TabsContext = createContext<{ tab: Tab; go: (t: Tab) => void } | null>(null);
export const useTabs = () => useContext(TabsContext);

const ICONS: Record<Tab, string> = { consult: "💬", care: "📋", settings: "⚙️" };

export function TabNav({ place }: { place: "top" | "bottom" }) {
  const ctx = useTabs();
  if (!ctx) return null;
  return (
    <nav className={place === "top" ? "tabs-top only-desktop" : "tabs-bottom only-mobile"} aria-label="메뉴">
      {TABS.map(([t, label]) => (
        <button key={t} type="button" aria-current={ctx.tab === t ? "page" : undefined} onClick={() => ctx.go(t)}>
          {place === "bottom" && <span aria-hidden="true" className="tab-icon">{ICONS[t]}</span>}
          <span>{label}</span>
        </button>
      ))}
    </nav>
  );
}
