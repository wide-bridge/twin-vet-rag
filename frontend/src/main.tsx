import { Component, lazy, StrictMode, Suspense, useEffect, useState, type ReactNode } from "react";
import { createRoot } from "react-dom/client";
import { App } from "./App";
import { Showcase } from "./components/Showcase";
import { VetQuestionPage } from "./components/VetLink";
import "./styles.css";

// PC shows the introduction + the app in a phone frame (an iframe of this same page). Inside that iframe,
// and on phones/tablets, the app itself is rendered.
const WIDE = "(min-width: 1024px)";
const framed = (() => {
  try {
    return window.self !== window.top || new URLSearchParams(window.location.search).has("frame");
  } catch {
    return true; // cross-origin parent: behave as the embedded app
  }
})();

// MVP1 HITL review screens are a separate app: on the review host (hitl-review.pawcheck.ai, any path) or on /review
// (local and preview deployments)
const ReviewApp = lazy(() => import("./review/ReviewApp").then((m) => ({ default: m.ReviewApp })));
const path = window.location.pathname.replace(/\/+$/, "");
const isReview = window.location.hostname.startsWith("hitl-review.") || path === "/review";
// M03 vet screens: on the vet host (vet.pawcheck.ai, any path) or on /vet (local and preview deployments)
const VetApp = lazy(() => import("./vet/VetApp").then((m) => ({ default: m.VetApp })));
const isVet = !isReview && (window.location.hostname.startsWith("vet.") || path === "/vet");
// M03 confirmation link for a guardian's question: /q/<token>
const questionToken = path.startsWith("/q/") ? decodeURIComponent(path.slice(3)) : null;
if (isReview || isVet || questionToken) {
  const robots = document.createElement("meta");
  robots.name = "robots";
  robots.content = "noindex, nofollow";
  document.head.appendChild(robots);
  document.title = isReview ? "TwinVet HITL Review" : isVet ? "TwinVet 수의사" : "수의사 질문 확인";
}

function Root() {
  if (questionToken) return <VetQuestionPage token={questionToken} />;
  if (isVet) {
    return (
      <Suspense fallback={null}>
        <VetApp />
      </Suspense>
    );
  }
  if (isReview) {
    return (
      <Suspense fallback={null}>
        <ReviewApp />
      </Suspense>
    );
  }
  return <GuardianRoot />;
}

function GuardianRoot() {
  const [wide, setWide] = useState(() => window.matchMedia(WIDE).matches);
  useEffect(() => {
    const mq = window.matchMedia(WIDE);
    const on = () => setWide(mq.matches);
    mq.addEventListener("change", on);
    return () => mq.removeEventListener("change", on);
  }, []);
  if (framed) document.documentElement.classList.add("in-frame");
  return wide && !framed ? <Showcase /> : <App />;
}

// A render error shows a recovery notice instead of a blank page (and the reason, for support)
class ErrorBoundary extends Component<{ children: ReactNode }, { error: string | null }> {
  state = { error: null as string | null };
  static getDerivedStateFromError(e: unknown) {
    return { error: e instanceof Error ? e.message : String(e) };
  }
  render() {
    if (!this.state.error) return this.props.children;
    return (
      <div style={{ padding: 24, maxWidth: 520, margin: "40px auto", fontFamily: "inherit" }} role="alert">
        <h1 style={{ fontSize: 20 }}>화면을 불러오지 못했어요</h1>
        <p>새로고침해도 계속되면 아래 내용을 캡처해 알려 주세요.</p>
        <button type="button" className="btn btn-primary" onClick={() => window.location.reload()}>새로고침</button>
        <pre style={{ whiteSpace: "pre-wrap", fontSize: 12, color: "#526078", marginTop: 16 }}>{this.state.error}</pre>
      </div>
    );
  }
}

const root = document.getElementById("root");
if (!root) throw new Error("root element missing");
createRoot(root).render(
  <StrictMode>
    <ErrorBoundary>
      <Root />
    </ErrorBoundary>
  </StrictMode>,
);
