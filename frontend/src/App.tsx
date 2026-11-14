// Screen switch: S01 start -> main tabs [상담 S02 | 케어 S08 | 설정 S09] / session-expired screen.
import { useCallback, useEffect, useState } from "react";
import { api, type Profile, type UiConfig } from "./api";
import { CareScreen } from "./components/CareScreen";
import { ConsultScreen } from "./components/ConsultScreen";
import { SettingsScreen } from "./components/SettingsScreen";
import { ExpiredScreen, StartScreen } from "./components/StartScreen";
import { TabNav, TabsContext, type Tab } from "./components/Tabs";
import { M03Provider } from "./components/VetLink";
import { store } from "./lib/store";

type View = "boot" | "start" | "main" | "expired";
const DEFAULT_CONFIG: UiConfig = { question_max_chars: 1000, session_ttl_days: 7, run_timeout_seconds: 120, profile_ttl_days: 90 };

export function App() {
  const [view, setView] = useState<View>("boot");
  const [config, setConfig] = useState<UiConfig>(DEFAULT_CONFIG);
  const [tab, setTab] = useState<Tab>("consult");
  // undefined while loading; null when nothing is saved for this browser
  const [profile, setProfile] = useState<Profile | null | undefined>(undefined);

  useEffect(() => {
    api.config().then(setConfig).catch(() => undefined);
    api.profile().then(setProfile).catch(() => setProfile(null));
    // a valid session + a remembered consultation resumes the conversation; otherwise show S01
    api.session()
      .then(() => setView("main"))
      .catch(() => setView(store.consultationId() ? "expired" : "start"));
  }, []);

  const go = useCallback((t: Tab) => {
    setTab(t);
    window.scrollTo(0, 0);
  }, []);
  const expired = useCallback(() => setView("expired"), []);
  const restart = async () => {
    store.setConsultationId(null);
    try {
      await api.startSession();
      setTab("consult");
      setView("main");
    } catch {
      setView("start");
    }
  };

  if (view === "boot" || profile === undefined) return null;
  if (view === "start") return <StartScreen ttlDays={config.session_ttl_days} onStarted={() => { setTab("consult"); setView("main"); }} />;
  if (view === "expired") return <ExpiredScreen onRestart={restart} />;
  return (
    <TabsContext.Provider value={{ tab, go }}>
      <M03Provider hasProfile={Boolean(profile)}>
      <div className="app-shell">
        <div className="app-main">
          {tab === "consult" && <ConsultScreen maxChars={config.question_max_chars} onExpired={expired} profile={profile} onProfile={setProfile} />}
          {tab === "care" && <CareScreen profile={profile} onExpired={expired} />}
          {tab === "settings" && <SettingsScreen profile={profile} onProfile={setProfile} ttlDays={config.profile_ttl_days ?? 90} />}
        </div>
        <TabNav place="bottom" />
      </div>
      </M03Provider>
    </TabsContext.Provider>
  );
}
