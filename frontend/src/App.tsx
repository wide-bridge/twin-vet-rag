import { useEffect, useState } from "react";
import { EmergencyNotice } from "./components/EmergencyNotice";

type Health = { status: "ok" | "degraded"; checks: Record<string, string> };

// Step-1 placeholder: verifies the Vite -> FastAPI proxy. S01/S02/S03 screens come in the API·UI step.
export function App() {
  const [health, setHealth] = useState<Health | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    fetch("/api/v1/health")
      .then(async (r) => setHealth((await r.json()) as Health))
      .catch(() => setError("서버에 연결할 수 없습니다."));
  }, []);

  return (
    <main>
      <h1>TwinVet Care</h1>
      <EmergencyNotice />
      <p>서버 상태: {error ?? (health ? health.status : "확인 중…")}</p>
    </main>
  );
}
