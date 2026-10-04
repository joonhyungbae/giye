// SPDX-License-Identifier: AGPL-3.0-only
import { createContext, useCallback, useContext, useEffect, useState, type ReactNode } from "react";

export type Lang = "ko" | "en";

type Ctx = {
  lang: Lang;
  setLang: (l: Lang) => void;
  t: (ko: string, en: string) => string;
};

const LangContext = createContext<Ctx>({
  lang: "en",
  setLang: () => {},
  t: (_ko, en) => en,
});

export function LanguageProvider({ children }: { children: ReactNode }) {
  // English is the default (user decision, 2026-10-04): the server renders English and a visitor's
  // stored choice ("giye-lang") switches to Korean after hydration.
  const [lang, setLangState] = useState<Lang>("en");

  useEffect(() => {
    const stored = window.localStorage.getItem("giye-lang");
    if (stored === "en" || stored === "ko") setLangState(stored);
  }, []);

  useEffect(() => {
    document.documentElement.setAttribute("lang", lang);
  }, [lang]);

  const setLang = useCallback((l: Lang) => {
    setLangState(l);
    window.localStorage.setItem("giye-lang", l);
  }, []);

  const t = useCallback((ko: string, en: string) => (lang === "ko" ? ko : en), [lang]);

  return <LangContext.Provider value={{ lang, setLang, t }}>{children}</LangContext.Provider>;
}

export function useLang() {
  return useContext(LangContext);
}

export const VERIFICATION_LABEL: Record<string, [string, string]> = {
  UNVERIFIED: ["미확인", "Unverified"],
  VERIFIED_BY_ARTIST: ["작가 확인", "Verified by artist"],
  SELF_SUBMITTED: ["본인 제출", "Self-submitted"],
};

export const SOURCE_TYPE_LABEL: Record<string, [string, string]> = {
  PUBLIC_RECORD: ["공개 기록", "Public record"],
  ARTIST_VERIFIED: ["작가 확인", "Artist verified"],
  SELF_SUBMITTED: ["본인 제출", "Self-submitted"],
};

export const ACTIVITY_TYPE_LABEL: Record<string, [string, string]> = {
  solo_exhibition: ["개인전", "Solo exhibition"],
  group_exhibition: ["단체전", "Group exhibition"],
  screening: ["상영", "Screening"],
  performance: ["퍼포먼스", "Performance"],
  festival: ["페스티벌", "Festival"],
  online_release: ["온라인 공개", "Online release"],
  award: ["수상", "Award"],
  residency: ["레지던시", "Residency"],
  other: ["기타", "Other"],
};

export const LINK_TYPE_LABEL: Record<string, [string, string]> = {
  website: ["웹사이트", "Website"],
  repository: ["저장소", "Repository"],
  video: ["영상", "Video"],
  social: ["소셜", "Social"],
  other: ["기타", "Other"],
};

export const REQUEST_TYPE_LABEL: Record<string, [string, string]> = {
  add: ["등재 제안", "Add a record"],
  correct: ["수정 요청", "Correction"],
  hide: ["비공개 요청", "Hide request"],
  self: ["본인 CV·웹사이트 알리기", "Share my CV or website"],
  same: ["같은 사람입니다", "These records are one person"],
};
