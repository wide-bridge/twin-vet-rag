// PC (>= 1024px): service introduction on the left, the real mobile app in a phone frame on the right
// (2026-11-12). The app runs inside a same-origin iframe, so it gets a phone-sized viewport and its mobile
// layout, and shares the session/profile cookies with a phone-width browser.
import { useState } from "react";
import { BrandLockup } from "./Common";

const FEATURES: [string, string, string][] = [
  ["📚", "근거 있는 AI 상담", "수의사 상담 사례·수의학 자료 기반"],
  ["🐶", "우리 아이 맞춤 답변", "품종·나이·건강상태 반영"],
  ["🏥", "주치동물병원 연결", "응급 시 바로 전화·카카오맵"],
  ["📍", "가까운 24시 동물병원", "내 위치 기준으로 찾기"],
  ["📋", "케어 기록", "상담마다 AI 요약 카드"],
  ["🩺", "병원에 보여주기", "진료 때 바로 보여줄 요약"],
];

// 관리자 상담 스위치 link (/?pilot=<code>) on PC: hand the code to the app inside the frame, then clean the address bar.
// Runs only when the showcase renders (phones read the code in the app itself).
function frameSrc(): string {
  try {
    const url = new URL(window.location.href);
    const code = url.searchParams.get("pilot");
    if (!code) return "/?frame=1";
    url.searchParams.delete("pilot");
    window.history.replaceState(null, "", url.pathname + url.search + url.hash);
    return "/?frame=1&pilot=" + encodeURIComponent(code);
  } catch {
    return "/?frame=1";
  }
}

export function Showcase() {
  const [src] = useState(frameSrc);
  return (
    <div className="showcase">
      <section className="showcase-info" aria-labelledby="showcase-h">
        <BrandLockup />
        <div className="showcase-dots" aria-hidden="true"><i /><b /><i /><b /><i /></div>
        <h1 id="showcase-h">반려견 건강 상담,<br /><em>주치동물병원</em>까지 이어지게</h1>
        <p className="showcase-lead">
          반려견 보호자를 위한 AI 건강 상담 서비스예요.<br />
          상담 자료로 답하고, 필요할 땐 우리 아이 동물병원으로 바로 연결해요.
        </p>
        <ul className="feature-grid">
          {FEATURES.map(([icon, title, sub]) => (
            <li key={title}>
              <span className="feature-icon" aria-hidden="true">{icon}</span>
              <span><strong>{title}</strong><small>{sub}</small></span>
            </li>
          ))}
        </ul>
        <div className="qr-card">
          <img src="/brand/qr-twinvet.png" alt="twinvet.pawcheck.ai QR 코드" width={96} height={96} />
          <div>
            <strong>휴대폰에서 바로 사용해 보세요</strong>
            <span>QR을 스캔하면 모바일에서 열려요</span>
            <span className="qr-url">twinvet.pawcheck.ai</span>
          </div>
        </div>
        <p className="showcase-fine">
          AI가 정리한 정보이며 진단이 아닙니다. 응급 상황에는 바로 동물병원에 연락하세요.<br />
          © 2026 TwinVet Care
        </p>
      </section>
      <div className="phone" aria-label="모바일 화면 미리보기">
        <span className="phone-notch" aria-hidden="true" />
        <iframe className="phone-app" src={src} title="TwinVet Care 모바일 앱" allow="geolocation" />
      </div>
    </div>
  );
}
