// S01 시작 (docs/05 §2, §8-3 copy).
import { useState } from "react";
import { api, type ApiError } from "../api";
import { EmergencyBox, Header } from "./Common";
import { IconDoc, IconError, IconLock, IconPaw, IconStethoscope } from "./Icons";

export function StartScreen({ ttlDays, onStarted }: { ttlDays: number; onStarted: () => void }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const start = async () => {
    setBusy(true);
    setError(null);
    try {
      await api.startSession();
      onStarted();
    } catch (e) {
      setError((e as ApiError).message || "상담을 시작하지 못했어요.");
    } finally {
      setBusy(false);
    }
  };

  return (
    <>
      <Header />
      <main className="page">
        <div className="start-head">
          <span className="eyebrow"><IconPaw small /> 반려견 전용 건강 가이드</span>
          <h1 className="title">반려견 건강 궁금증, 상담 자료로 함께 찾아봐요</h1>
        </div>
        <ul className="card info-list">
          <li><span className="info-icon"><IconDoc /></span><span>반려견(강아지)의 일반 건강 질문을 도와드려요. 내과·외과·피부·눈·치아 관련 상담 자료를 바탕으로 답합니다.</span></li>
          <li><span className="info-icon"><IconStethoscope /></span><span>AI가 상담 사례와 수의학 자료를 찾아 정리한 정보예요. 진단이나 처방이 아니며, 정확한 판단은 동물병원에서 받으세요.</span></li>
          <li><span className="info-icon"><IconLock /></span><span>질문과 입력한 반려견 정보는 답변을 위해 저장돼요. 이 브라우저에서 {ttlDays}일간 볼 수 있고 이후 자동 삭제되며, 언제든 직접 삭제할 수 있어요.</span></li>
        </ul>
        <EmergencyBox />
        <div className="start-actions">
          <button type="button" className="btn btn-primary btn-lg" onClick={start} disabled={busy}>
            {busy ? "시작하는 중…" : "상담 시작"}
          </button>
          {error && (
            <p className="error-text" role="alert" style={{ justifyContent: "center" }}>
              <IconError small /> {error}
              <button type="button" className="btn btn-ghost" onClick={start}>다시 시도</button>
            </p>
          )}
        </div>
      </main>
    </>
  );
}

export function ExpiredScreen({ onRestart }: { onRestart: () => void }) {
  return (
    <>
      <Header />
      <main className="center-screen">
        <span className="info-icon"><IconLock /></span>
        <h1 style={{ fontSize: 20, margin: 0 }}>상담 시간이 만료되어 이전 대화는 볼 수 없어요.</h1>
        <button type="button" className="btn btn-primary btn-lg" onClick={onRestart}>새 상담 시작</button>
      </main>
    </>
  );
}
