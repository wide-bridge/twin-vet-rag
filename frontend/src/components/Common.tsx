import { useEffect, useRef, useState, type ReactNode } from "react";
import { IconMore, IconWarning } from "./Icons";

export const EMERGENCY_TITLE = "응급 상황이면 AI 답변을 기다리지 마세요.";
export const EMERGENCY_BODY = "호흡 곤란, 의식 저하, 경련, 심한 출혈, 중독 의심 등은 바로 동물병원(야간에는 24시 동물병원)에 연락하세요.";

export function BrandLockup() {
  return (
    <a className="brand-lockup" href="/" aria-label="TwinVet Care 처음으로">
      <img src="/brand/twinvet-logo.png" alt="" width={683} height={283} />
      <span className="care" aria-hidden="true">Care</span>
    </a>
  );
}

type Action = { label: string; onClick: () => void; danger?: boolean };

export function Header({ actions = [], children }: { actions?: Action[]; children?: ReactNode }) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!open) return;
    const close = (e: MouseEvent | KeyboardEvent) => {
      if (e instanceof KeyboardEvent ? e.key === "Escape" : !ref.current?.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", close);
    document.addEventListener("keydown", close);
    return () => {
      document.removeEventListener("mousedown", close);
      document.removeEventListener("keydown", close);
    };
  }, [open]);
  return (
    <header className="header">
      <div className="header-inner">
        <BrandLockup />
        {actions.length > 0 && (
          <>
            <div className="header-actions only-desktop">
              {actions.map((a) => (
                <button key={a.label} type="button" className="btn" onClick={a.onClick}>{a.label}</button>
              ))}
            </div>
            <div className="menu only-mobile" ref={ref}>
              <button type="button" className="btn btn-ghost btn-icon" aria-label="메뉴" aria-haspopup="menu" aria-expanded={open} onClick={() => setOpen(!open)}>
                <IconMore />
              </button>
              {open && (
                <ul className="menu-list" role="menu">
                  {actions.map((a) => (
                    <li key={a.label} role="none">
                      <button type="button" role="menuitem" onClick={() => { setOpen(false); a.onClick(); }}>{a.label}</button>
                    </li>
                  ))}
                </ul>
              )}
            </div>
          </>
        )}
      </div>
      {children}
    </header>
  );
}

export function EmergencyBox() {
  return (
    <aside className="emergency-box" role="note" aria-label="응급 상황 안내">
      <IconWarning />
      <div>
        <strong>{EMERGENCY_TITLE}</strong>
        {EMERGENCY_BODY}
      </div>
    </aside>
  );
}

export function Dialog({ title, children, actions, onClose, sheet }: {
  title: string; children?: ReactNode; actions: ReactNode; onClose: () => void; sheet?: boolean;
}) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const prev = document.activeElement as HTMLElement | null;
    ref.current?.querySelector<HTMLElement>("button")?.focus();
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("keydown", onKey);
      prev?.focus();
    };
  }, [onClose]);
  return (
    <div className={"overlay" + (sheet ? " sheet" : "")} onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <div className="dialog" role="dialog" aria-modal="true" aria-labelledby="dlg-title" ref={ref}>
        <h2 id="dlg-title">{title}</h2>
        {children}
        <div className="dialog-actions">{actions}</div>
      </div>
    </div>
  );
}
