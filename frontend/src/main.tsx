import { lazy, StrictMode, Suspense, useEffect, useState } from "react";
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

const root = document.getElementById("root");
if (!root) throw new Error("root element missing");
createRoot(root).render(
  <StrictMode>
    <Root />
  </StrictMode>,
);
