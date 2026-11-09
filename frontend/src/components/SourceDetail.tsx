// S03 근거 상세 (docs/05 §4): full-screen modal below 1024px, right panel on PC. Title and source only;
// the passage is shown only if the server returns one (never fetched and hidden).
import { useEffect, useRef, useState } from "react";
import { api, type ApiError, type CitationDetail, type CitationRef } from "../api";
import { IconBack, IconClose, IconError, IconInfo } from "./Icons";

export function SourceDetail({ citation, label, onClose }: { citation: CitationRef; label: string; onClose: () => void }) {
  const [data, setData] = useState<CitationDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [attempt, setAttempt] = useState(0);
  const closeRef = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    let alive = true;
    setData(null);
    setError(null);
    api.citation(citation.id).then((d) => alive && setData(d)).catch((e: ApiError) => alive && setError(e.message));
    return () => {
      alive = false;
    };
  }, [citation.id, attempt]);

  useEffect(() => {
    closeRef.current?.focus();
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [onClose]);

  return (
    <aside className="source-panel" role="dialog" aria-modal="false" aria-labelledby="src-title">
      <header>
        <button ref={closeRef} type="button" className="btn btn-ghost btn-icon only-mobile" aria-label="닫기" onClick={onClose}><IconBack /></button>
        <h2 id="src-title">근거 상세</h2>
        <button type="button" className="btn btn-ghost btn-icon only-desktop" aria-label="닫기" onClick={onClose}><IconClose /></button>
      </header>
      <div className="source-content">
        <p className="help" style={{ margin: 0 }}>{label}</p>
        {!data && !error && <p className="help">불러오는 중…</p>}
        {error && (
          <div className="banner" role="alert">
            <span style={{ display: "flex", gap: 6, alignItems: "center" }}><IconError small /> 근거를 불러오지 못했어요.</span>
            <button type="button" className="btn" onClick={() => setAttempt(attempt + 1)}>다시 시도</button>
          </div>
        )}
        {data && data.availability === "unavailable" && (
          <div className="note"><IconInfo /> 현재 제공할 수 없는 자료예요.</div>
        )}
        {data && data.availability === "available" && (
          <>
            <dl className="kv">
              <dt>자료 유형</dt><dd>{data.document_type === "reference" ? "전문 자료" : "상담 사례"}</dd>
              <dt>자료명</dt><dd>{data.title}</dd>
              <dt>출처</dt><dd>{data.source_name}</dd>
              {data.department && (<><dt>진료과</dt><dd>{data.department}</dd></>)}
              {data.source_uri && (<><dt>원문</dt><dd><a href={data.source_uri} target="_blank" rel="noopener noreferrer">원문 보기</a></dd></>)}
            </dl>
            {data.excerpt ? (
              <blockquote className="note" style={{ margin: 0, whiteSpace: "pre-wrap" }}>{data.excerpt}</blockquote>
            ) : (
              <div className="note"><IconInfo /> 원문 구절은 자료 이용 조건 확인 후 제공됩니다.</div>
            )}
          </>
        )}
      </div>
    </aside>
  );
}
