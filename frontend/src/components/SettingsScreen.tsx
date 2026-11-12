// S09 설정 (docs/05 §3-2): the saved dog and 주치동물병원. Changes apply from the next consultation.
// Clinic search: Kakao results are shown live; we keep only the public-data clinic (matched on the server),
// or a name the guardian types, plus Kakao's place id/URL. The phone is typed (or confirmed) by the guardian.
import { useState } from "react";
import { api, type ApiError, type Clinic, type ClinicChoice, type Place, type Profile } from "../api";
import { currentPosition, distanceText, kakaoMapHref } from "../lib/care";
import { emptyDogForm, formFromDog, validateDog, type DogForm } from "../lib/dogForm";
import { store } from "../lib/store";
import { Dialog, Header } from "./Common";
import { DogCard } from "./DogCard";
import { IconError, IconInfo } from "./Icons";

const PHONE = /^[0-9-]{7,20}$/;
const phoneValid = (p: string) => p === "" || (PHONE.test(p) && p.replace(/\D/g, "").length >= 7);
const NAME_FORBIDDEN = /[\u0000-\u001f\u007f<>[\]{}`\\]/;

// what the guardian picked in this visit (undefined = unchanged)
type Pick =
  | { kind: "public"; clinic: Clinic; place: Place | null }
  | { kind: "manual"; place: Place | null };

function ClinicSearch({ onPick, onManual }: { onPick: (p: Pick) => void; onManual: () => void }) {
  const [q, setQ] = useState("");
  const [items, setItems] = useState<Place[] | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const run = async (near: boolean) => {
    setBusy(true);
    setError(null);
    try {
      let pos: { x: number; y: number } | undefined;
      if (near) {
        try {
          pos = await currentPosition();
        } catch {
          setError("위치를 확인할 수 없어요. 병원 이름이나 동네로 검색해 주세요.");
          return;
        }
      }
      const { items } = await api.searchClinics({ q: near ? undefined : q.trim(), ...pos });
      setItems(items);
    } catch (e) {
      setError((e as ApiError).message);
    } finally {
      setBusy(false);
    }
  };
  const choose = async (p: Place) => {
    setBusy(true);
    try {
      const { clinic } = await api.matchClinic(p);
      onPick(clinic ? { kind: "public", clinic, place: p } : { kind: "manual", place: p });
    } catch (e) {
      setError((e as ApiError).message);
    } finally {
      setBusy(false);
    }
  };
  return (
    <div className="clinic-search">
      <form className="search-row" onSubmit={(e) => { e.preventDefault(); if (q.trim()) run(false); }}>
        <label htmlFor="clinic-q" className="sr-only">병원 이름 또는 동네</label>
        <input id="clinic-q" className="input" placeholder="병원 이름 또는 동네 (예: 망원동)" maxLength={60} value={q}
          onChange={(e) => setQ(e.target.value)} autoComplete="off" />
        <button type="submit" className="btn btn-primary" disabled={busy || !q.trim()}>검색</button>
      </form>
      <button type="button" className="btn btn-block" onClick={() => run(true)} disabled={busy}>📍 내 주변 동물병원</button>
      {busy && <p className="help">찾는 중…</p>}
      {error && <p className="error-text" role="alert"><IconError small /> {error}</p>}
      {items && !busy && (
        items.length === 0 ? <p className="help">검색 결과가 없어요. 다른 이름이나 동네로 찾아보세요.</p> : (
          <ul className="place-list" aria-label="검색 결과">
            {items.map((p) => (
              <li key={p.place_id}>
                <button type="button" className="place-pick" onClick={() => choose(p)}>
                  <strong>{p.name}</strong>
                  <span className="help">{[distanceText(p.distance_m), p.road_address || p.address, p.phone].filter(Boolean).join(" · ")}</span>
                </button>
              </li>
            ))}
          </ul>
        )
      )}
      {items && <p className="fine">장소 검색: 카카오</p>}
      <button type="button" className="link" onClick={onManual}>못 찾겠어요 → 이름 직접 입력</button>
    </div>
  );
}

export function SettingsScreen({ profile, onProfile, ttlDays }: {
  profile: Profile | null; onProfile: (p: Profile | null) => void; ttlDays: number;
}) {
  const [form, setForm] = useState<DogForm>(() => {
    if (profile) return formFromDog(profile.dog);
    const saved = store.lastDog();
    return saved ? formFromDog(saved.dog, saved.savedAt) : emptyDogForm();
  });
  const [clinic, setClinic] = useState<Clinic | null>(profile?.clinic ?? null); // currently shown saved/public clinic
  const [pick, setPick] = useState<Pick | null | undefined>(undefined); // undefined: unchanged, null: removed
  const [manualName, setManualName] = useState("");
  const [phone, setPhone] = useState(profile?.clinic_phone ?? "");
  const [searching, setSearching] = useState(false);
  const [saving, setSaving] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [tried, setTried] = useState(false);

  const validation = validateDog(form);
  const nameError = pick?.kind === "manual" && (!manualName.trim() ? "병원 이름을 입력해 주세요"
    : manualName.trim().length > 40 ? "40자 이내로 입력해 주세요" : NAME_FORBIDDEN.test(manualName) ? "쓸 수 없는 기호가 있어요" : null);
  const phoneError = phoneValid(phone.trim()) ? null : "숫자와 하이픈(-)만 7~20자로 입력해 주세요";
  const hasClinic = pick === undefined ? Boolean(clinic) : pick !== null;

  const onPicked = (p: Pick) => {
    setPick(p);
    setSearching(false);
    setMessage(null);
    if (p.kind === "public") {
      setClinic(p.clinic);
      setPhone(p.clinic.public_phone ?? ""); // public data may be prefilled; Kakao's number is only shown as a hint
    } else {
      setClinic(null);
      setManualName("");
      setPhone("");
    }
  };

  const save = async () => {
    setTried(true);
    setMessage(null);
    setError(null);
    if (!validation.dog || nameError || phoneError) return;
    let choice: ClinicChoice | null | undefined;
    if (pick === null) choice = null;
    else if (pick?.kind === "public") choice = { clinic_id: pick.clinic.id, kakao_place_id: pick.place?.place_id ?? null, kakao_place_url: pick.place?.place_url ?? null };
    else if (pick?.kind === "manual") choice = { manual_name: manualName.trim(), kakao_place_id: pick.place?.place_id ?? null, kakao_place_url: pick.place?.place_url ?? null };
    setSaving(true);
    try {
      const body = { dog: validation.dog, clinic_phone: hasClinic ? phone.trim() || null : null, ...(choice !== undefined ? { clinic: choice } : {}) };
      const p = await api.saveProfile(body);
      onProfile(p);
      store.setLastDog(null);
      setClinic(p.clinic);
      setPick(undefined);
      setPhone(p.clinic_phone ?? "");
      setTried(false);
      setMessage("저장했어요. 다음 상담부터 적용돼요.");
    } catch (e) {
      setError((e as ApiError).message);
    } finally {
      setSaving(false);
    }
  };

  const removeProfile = async () => {
    try {
      await api.deleteProfile();
      onProfile(null);
      store.setLastDog(null);
      setForm(emptyDogForm());
      setClinic(null);
      setPick(undefined);
      setPhone("");
      setMessage("저장된 정보를 삭제했어요.");
    } catch (e) {
      setError((e as ApiError).message);
    } finally {
      setConfirmDelete(false);
    }
  };

  const placeHint = pick ? pick.place : null;
  return (
    <div className="page-shell">
      <Header />
      <main className="page settings">
        <h1 className="page-title">설정</h1>
        <p className="help"><IconInfo small /> 여기 저장한 정보로 상담을 시작해요. 바꾸면 다음 상담부터 적용되고 지난 상담 기록은 그대로예요.</p>

        <DogCard form={form} errors={tried ? validation.errors : {}} onChange={setForm} />

        <section className="card settings-card" aria-labelledby="clinic-h">
          <h2 id="clinic-h">주치동물병원 <small>(선택)</small></h2>
          <p className="help">평소 다니는 동물병원을 등록하면 응급 안내에서 바로 전화할 수 있어요.</p>

          {hasClinic && !searching && (
            <div className="clinic-box">
              {pick?.kind === "manual" ? (
                <div className="field">
                  <label htmlFor="clinic-name">병원 이름</label>
                  <p className="help">공공데이터에서 이 병원을 찾지 못했어요. 병원 이름을 직접 입력해 주세요.</p>
                  <input id="clinic-name" className="input" maxLength={40} value={manualName} placeholder={pick.place?.name ?? "예: 행복동물병원"}
                    onChange={(e) => setManualName(e.target.value)} aria-invalid={Boolean(tried && nameError)} autoComplete="off" />
                  {tried && nameError && <p className="error-text"><IconError small /> {nameError}</p>}
                </div>
              ) : clinic && (
                <>
                  <strong>{clinic.name}</strong>
                  {clinic.road_address && <span className="help">{clinic.road_address}</span>}
                  {clinic.business_status === "closed" && <p className="error-text"><IconError small /> 공공데이터에 폐업으로 표시된 병원이에요. 다시 찾아 주세요.</p>}
                  <a className="link" href={kakaoMapHref(clinic)} target="_blank" rel="noopener noreferrer">카카오맵에서 보기</a>
                </>
              )}
              <div className="field">
                <label htmlFor="clinic-phone">전화번호</label>
                <input id="clinic-phone" className="input" inputMode="tel" maxLength={20} placeholder="예: 02-123-4567" value={phone}
                  onChange={(e) => setPhone(e.target.value.replace(/[^\d-]/g, ""))} aria-invalid={Boolean(tried && phoneError)} />
                {placeHint?.phone && <p className="help">검색 결과의 번호: {placeHint.phone} (확인 후 입력해 주세요)</p>}
                {tried && phoneError && <p className="error-text"><IconError small /> {phoneError}</p>}
              </div>
              <p className="fine">TwinVet이 확인한 정보가 아니며 보호자가 등록한 병원이에요.</p>
              <div className="row-actions">
                <button type="button" className="btn" onClick={() => setSearching(true)}>다른 병원 찾기</button>
                <button type="button" className="btn btn-ghost" onClick={() => { setPick(null); setClinic(null); setPhone(""); }}>등록 해제</button>
              </div>
            </div>
          )}
          {(!hasClinic || searching) && (
            <ClinicSearch onPick={onPicked} onManual={() => onPicked({ kind: "manual", place: null })} />
          )}
          {searching && hasClinic && <button type="button" className="link" onClick={() => setSearching(false)}>검색 닫기</button>}
        </section>

        {error && <p className="error-text" role="alert"><IconError small /> {error}</p>}
        {message && <p className="ok-text" role="status">{message}</p>}
        <div className="settings-actions">
          <button type="button" className="btn btn-primary btn-lg btn-block" onClick={save} disabled={saving}>{saving ? "저장하는 중…" : "저장"}</button>
        </div>
        <p className="help"><IconInfo small /> 이 브라우저에서만 불러와요. 마지막 사용 후 {ttlDays}일이 지나면 자동으로 삭제돼요.</p>
        {profile && <button type="button" className="link danger" onClick={() => setConfirmDelete(true)}>저장된 정보 삭제</button>}
      </main>
      {confirmDelete && (
        <Dialog title="저장된 정보를 삭제할까요?" onClose={() => setConfirmDelete(false)} sheet
          actions={<>
            <button type="button" className="btn" onClick={() => setConfirmDelete(false)}>취소</button>
            <button type="button" className="btn btn-danger" onClick={removeProfile}>삭제</button>
          </>}>
          <p>반려견 정보와 주치동물병원이 삭제돼요. 지난 상담 기록은 각자의 보관 기간까지 남아요.</p>
        </Dialog>
      )}
    </div>
  );
}
