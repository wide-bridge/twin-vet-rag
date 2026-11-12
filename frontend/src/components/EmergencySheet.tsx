// 응급 안내 하단 시트 (docs/05 §3, 2026-11-12): 주치동물병원 전화 · 카카오맵 · 가까운 24시 동물병원 · 전화할 때 말할 것.
// Never asks for input in an emergency: without a registered clinic the 24h search comes first.
import { useState } from "react";
import { api, type Place, type Profile } from "../api";
import { dogSummary } from "../lib/age";
import { KAKAO_24H_SEARCH, clinicPhone, currentPosition, distanceText, kakaoMapHref, telHref } from "../lib/care";
import { Dialog, EMERGENCY_BODY, EMERGENCY_TITLE } from "./Common";

type NearState = { kind: "idle" } | { kind: "loading" } | { kind: "done"; items: Place[] } | { kind: "error"; text: string };

function Nearby24h() {
  const [s, setS] = useState<NearState>({ kind: "idle" });
  const find = async () => {
    setS({ kind: "loading" });
    let pos: { x: number; y: number };
    try {
      pos = await currentPosition();
    } catch {
      setS({ kind: "error", text: "위치를 확인할 수 없어요. 카카오맵에서 24시 동물병원을 찾아 주세요." });
      return;
    }
    try {
      const { items } = await api.searchClinics({ emergency: true, ...pos });
      setS({ kind: "done", items: items.slice(0, 5) });
    } catch {
      setS({ kind: "error", text: "지금은 검색할 수 없어요. 카카오맵에서 24시 동물병원을 찾아 주세요." });
    }
  };
  return (
    <section className="sheet-section">
      {s.kind !== "done" && (
        <button type="button" className="btn btn-block" onClick={find} disabled={s.kind === "loading"}>
          {s.kind === "loading" ? "가까운 24시 동물병원을 찾는 중…" : "📍 가까운 24시 동물병원 찾기"}
        </button>
      )}
      {s.kind === "error" && (
        <p className="help">{s.text} <a href={KAKAO_24H_SEARCH} target="_blank" rel="noopener noreferrer">카카오맵 열기</a></p>
      )}
      {s.kind === "done" && (
        s.items.length === 0 ? (
          <p className="help">근처 10km 안에서 찾지 못했어요. <a href={KAKAO_24H_SEARCH} target="_blank" rel="noopener noreferrer">카카오맵에서 찾기</a></p>
        ) : (
          <ul className="place-list" aria-label="가까운 24시 동물병원">
            {s.items.map((p) => (
              <li key={p.place_id}>
                <div className="place-main">
                  <strong>{p.name}</strong>
                  <span className="help">{[distanceText(p.distance_m), p.road_address || p.address].filter(Boolean).join(" · ")}</span>
                </div>
                <div className="place-actions">
                  {p.phone && <a className="btn btn-primary" href={telHref(p.phone)}>📞 전화</a>}
                  <a className="btn" href={p.place_url} target="_blank" rel="noopener noreferrer">지도</a>
                </div>
              </li>
            ))}
          </ul>
        )
      )}
      {s.kind === "done" && <p className="fine">장소 정보: 카카오. 영업 여부는 전화로 확인해 주세요.</p>}
    </section>
  );
}

export function EmergencySheet({ profile, onClose, onRegister }: { profile: Profile | null; onClose: () => void; onRegister: () => void }) {
  const clinic = profile?.clinic ?? null;
  const phone = clinicPhone(clinic, profile?.clinic_phone ?? null);
  const dog = profile?.dog;
  return (
    <Dialog title={EMERGENCY_TITLE} onClose={onClose} sheet
      actions={<button type="button" className="btn" onClick={onClose}>닫기</button>}>
      <p>{EMERGENCY_BODY}</p>
      {clinic && (
        <section className="sheet-section clinic-box" aria-label="주치동물병원">
          <span className="label">주치동물병원</span>
          <strong>{clinic.name}</strong>
          {clinic.road_address && <span className="help">{clinic.road_address}</span>}
          <div className="place-actions">
            {phone ? <a className="btn btn-primary" href={telHref(phone)}>📞 {phone}</a> : <span className="help">등록된 전화번호가 없어요</span>}
            <a className="btn" href={kakaoMapHref(clinic)} target="_blank" rel="noopener noreferrer">카카오맵에서 보기</a>
          </div>
        </section>
      )}
      <Nearby24h />
      {!clinic && (
        <p className="help">
          <button type="button" className="link" onClick={() => { onClose(); onRegister(); }}>주치동물병원 등록하기</button>
          {" "}— 평소에 등록해 두면 응급 시 바로 전화할 수 있어요
        </p>
      )}
      <details className="sheet-section">
        <summary>전화할 때 말할 것</summary>
        <ul className="say-list">
          {dog && <li>반려견: {dogSummary(dog)}</li>}
          <li>지금 보이는 증상과 시작된 시각</li>
          <li>먹은 것·먹은 양 (중독이 의심되면 포장지나 남은 것을 챙기세요)</li>
          <li>기존 질환·복용 중인 약{dog?.health_notes ? `: ${dog.health_notes}` : ""}</li>
        </ul>
      </details>
    </Dialog>
  );
}
