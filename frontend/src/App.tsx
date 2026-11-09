// Screen switch: S01 start -> S02 consult (S03 opens inside it) / session-expired screen.
import { useCallback, useEffect, useState } from "react";
import { api, type UiConfig } from "./api";
import { ConsultScreen } from "./components/ConsultScreen";
import { ExpiredScreen, StartScreen } from "./components/StartScreen";
import { store } from "./lib/store";

type View = "boot" | "start" | "consult" | "expired";
const DEFAULT_CONFIG: UiConfig = { question_max_chars: 1000, session_ttl_days: 7, run_timeout_seconds: 120 };

export function App() {
  const [view, setView] = useState<View>("boot");
  const [config, setConfig] = useState<UiConfig>(DEFAULT_CONFIG);

  useEffect(() => {
    api.config().then(setConfig).catch(() => undefined);
    // a valid session + a remembered consultation resumes the conversation; otherwise show S01
    api.session()
      .then(() => setView("consult"))
      .catch(() => setView(store.consultationId() ? "expired" : "start"));
  }, []);

  const expired = useCallback(() => setView("expired"), []);
  const restart = async () => {
    store.setConsultationId(null);
    try {
      await api.startSession();
      setView("consult");
    } catch {
      setView("start");
    }
  };

  if (view === "boot") return null;
  if (view === "start") return <StartScreen ttlDays={config.session_ttl_days} onStarted={() => setView("consult")} />;
  if (view === "expired") return <ExpiredScreen onRestart={restart} />;
  return <ConsultScreen maxChars={config.question_max_chars} onExpired={expired} />;
}
