// SPDX-License-Identifier: AGPL-3.0-only
import { Canvas } from "@react-three/fiber";
import { Link } from "@tanstack/react-router";
import { useCallback, useEffect, useMemo, useRef, useState, type FormEvent } from "react";
import * as THREE from "three";
import { useLang } from "@/lib/i18n";
import { SITE_NAV } from "@/components/SiteChrome";
import { searchPoints, type EmbeddingPoint, type EmbeddingSpace } from "@/lib/flightSearch";
import { Birds } from "./Birds";
import { FlightRig } from "./FlightRig";
import { Atmosphere, Clouds, Motes } from "./Sky";
import { Bloom } from "./Bloom";
import { createFlightState, type FlightState } from "./flightState";
import { loadBirdSpecies, type BirdSpecies } from "./birdModels";
import { createWind, type Wind } from "./wind";
import { playCall, playFlap } from "./sound";

const SKY_CSS = "linear-gradient(180deg, #5A9AD3 0%, #B6D4EC 50%, #F3E4D2 100%)";

type Msg = {
  id: number;
  role: "you" | "guide";
  text: string;
  chips?: EmbeddingPoint[];
};

function isTypingTarget(t: EventTarget | null) {
  const el = t as HTMLElement | null;
  if (!el) return false;
  const tag = el.tagName;
  return tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT" || el.isContentEditable;
}

export function FlightArchive({ space }: { space: EmbeddingSpace }) {
  const { lang, setLang, t } = useLang();
  const [selected, setSelected] = useState<EmbeddingPoint | null>(null);
  const [query, setQuery] = useState("");
  const [hits, setHits] = useState<Array<EmbeddingPoint & { score: number }>>([]);
  const [log, setLog] = useState<Msg[]>([]);
  const [canvasReady, setCanvasReady] = useState(false);
  const [species, setSpecies] = useState<BirdSpecies[] | null>(null);
  const [windOn, setWindOn] = useState(false);
  const [hud, setHud] = useState({
    altitude: 0,
    speed: 0,
    perched: false,
    flying: false,
    interacted: false,
  });
  const keys = useRef<Record<string, boolean>>({});
  const flight = useMemo<FlightState>(() => createFlightState(), []);
  const wind = useRef<Wind | null>(null);
  const msgId = useRef(0);
  const callRef = useRef<() => void>(() => {});
  const calledOnce = useRef(false);

  const say = useCallback((m: Omit<Msg, "id">) => {
    msgId.current += 1;
    const id = msgId.current;
    setLog((prev) => [...prev, { ...m, id }].slice(-4));
  }, []);

  // Keyboard: ignore while typing in the guide bar; stop arrows/space scrolling the page.
  useEffect(() => {
    const down = (e: KeyboardEvent) => {
      if (isTypingTarget(e.target)) {
        if (e.key === "Escape") (e.target as HTMLElement).blur();
        return;
      }
      const key = e.key.toLowerCase();
      if (key.startsWith("arrow") || key === " ") e.preventDefault();
      if (key === "/" || key === "enter") {
        e.preventDefault();
        document.getElementById("sky-q")?.focus();
        return;
      }
      if (key === " ") {
        if (!e.repeat) {
          flight.flapRequested = true;
          playFlap();
        }
        return;
      }
      if (key === "c") {
        if (!e.repeat) callRef.current();
        return;
      }
      keys.current[key] = true;
      if (key === "escape") setSelected(null);
    };
    const up = (e: KeyboardEvent) => {
      keys.current[e.key.toLowerCase()] = false;
    };
    const blur = () => {
      keys.current = {};
    };
    window.addEventListener("keydown", down);
    window.addEventListener("keyup", up);
    window.addEventListener("blur", blur);
    const id = requestAnimationFrame(() => setCanvasReady(true));
    return () => {
      window.removeEventListener("keydown", down);
      window.removeEventListener("keyup", up);
      window.removeEventListener("blur", blur);
      cancelAnimationFrame(id);
    };
  }, [flight]);

  // Low-rate mirror of flight state for the HUD.
  useEffect(() => {
    const id = window.setInterval(() => {
      setHud((h) => {
        const next = {
          altitude: Math.round(flight.altitude),
          speed: Math.round(flight.speed),
          perched: flight.perched,
          flying: flight.flying,
          interacted: flight.interacted,
        };
        return h.altitude === next.altitude &&
          h.speed === next.speed &&
          h.perched === next.perched &&
          h.flying === next.flying &&
          h.interacted === next.interacted
          ? h
          : next;
      });
      wind.current?.set(flight.speed01);
    }, 120);
    return () => window.clearInterval(id);
  }, [flight]);

  useEffect(() => () => wind.current?.stop(), []);

  // Bird models (free three.js morph-target birds). Falls back to silhouettes on failure.
  useEffect(() => {
    let alive = true;
    loadBirdSpecies()
      .then((list) => {
        if (alive) setSpecies(list);
      })
      .catch((err) => {
        console.warn("bird models failed to load; using silhouettes", err);
        if (alive) setSpecies([]);
      });
    return () => {
      alive = false;
    };
  }, []);

  const name = useCallback(
    (p: EmbeddingPoint) =>
      (lang === "ko" ? p.name_ko : p.name_en) || p.name_ko || p.name_en || p.id,
    [lang],
  );
  const flockName = useCallback(
    (p: EmbeddingPoint) => {
      const c = space.clusters.find((x) => x.id === p.cluster);
      if (!c) return "";
      return (lang === "ko" ? c.name_ko : c.name_en) || c.name_ko;
    },
    [space.clusters, lang],
  );

  // Guide comment once we are flying alongside the chosen bird.
  const wasPerched = useRef(false);
  useEffect(() => {
    if (hud.perched && !wasPerched.current && selected) {
      say({
        role: "guide",
        text: t(
          `${name(selected)} 곁에서 함께 날고 있어요. 기록 보기에서 출처가 붙은 이력을 볼 수 있어요.`,
          `Flying alongside ${name(selected)}. Open the record for the sourced history.`,
        ),
      });
    }
    wasPerched.current = hud.perched;
  }, [hud.perched, selected, say, name, t]);

  const doCall = useCallback(() => {
    flight.callAt = performance.now();
    flight.interacted = true;
    playCall();
    if (!calledOnce.current) {
      calledOnce.current = true;
      say({
        role: "guide",
        text: t(
          "빛의 고리가 퍼지면 가까운 새들이 잠시 곁으로 옵니다. 가까이 온 새의 이름이 떠오릅니다.",
          "The ring of light draws nearby birds to you for a while. Names appear on those who come close.",
        ),
      });
    }
  }, [flight, say, t]);
  callRef.current = doCall;

  const toggleWind = async () => {
    if (windOn) {
      wind.current?.stop();
      wind.current = null;
      setWindOn(false);
      return;
    }
    wind.current = createWind();
    await wind.current.start();
    setWindOn(true);
  };

  const flyTo = useCallback(
    (p: EmbeddingPoint, announce = true) => {
      setSelected(p);
      flight.pendingIndex = space.points.findIndex((x) => x.id === p.id);
      flight.interacted = true;
      if (announce) {
        const tags = [...p.medium_tags, ...p.regions].slice(0, 3).join(" · ");
        say({
          role: "guide",
          text: t(
            `${name(p)} 곁으로 날아갑니다${flockName(p) ? ` — ${flockName(p)} 무리` : ""}${tags ? `, ${tags}` : ""}.`,
            `Gliding to ${name(p)}${flockName(p) ? ` — ${flockName(p)} flock` : ""}${tags ? `, ${tags}` : ""}.`,
          ),
        });
      }
    },
    [flight, space.points, say, name, flockName, t],
  );

  const onSearch = (e: FormEvent) => {
    e.preventDefault();
    const q = query.trim();
    if (!q) return;
    say({ role: "you", text: q });
    const found = searchPoints(space, q, 8);
    setHits(found);
    setQuery("");
    (e.currentTarget as HTMLFormElement).querySelector("input")?.blur();
    if (!found[0]) {
      say({
        role: "guide",
        text: t(
          "그 이름의 새는 이 하늘에 없어요. 매체(XR, 사운드), 지역(서울), 표집틀(APE)로 물어보거나 목록에서 찾아보세요.",
          "No bird by that name up here. Try a medium (XR, sound), a region (Seoul), a frame (APE), or the list.",
        ),
      });
      return;
    }
    const first = found[0];
    const others = found.slice(1, 6);
    const tags = [...first.medium_tags, ...first.regions].slice(0, 3).join(" · ");
    say({
      role: "guide",
      text: t(
        `${name(first)} 곁으로 날아갑니다${flockName(first) ? ` — ${flockName(first)} 무리` : ""}${tags ? `, ${tags}` : ""}.${others.length ? " 다른 후보:" : ""}`,
        `Gliding to ${name(first)}${flockName(first) ? ` — ${flockName(first)} flock` : ""}${tags ? `, ${tags}` : ""}.${others.length ? " Also:" : ""}`,
      ),
      chips: others,
    });
    flyTo(first, false);
  };

  const highlightIds = useMemo(() => new Set(hits.map((h) => h.id)), [hits]);
  const showHint = !hud.interacted;

  return (
    <div
      className="relative h-dvh min-h-[30rem] w-full select-none overflow-hidden"
      style={{ background: SKY_CSS }}
    >
      {canvasReady && species !== null ? (
        <Canvas
          camera={{ position: [0, 11, 46], fov: 60, near: 0.1, far: 260 }}
          dpr={[1, 1.75]}
          gl={{ antialias: true, powerPreference: "high-performance", alpha: false }}
          onCreated={({ gl }) => {
            gl.toneMapping = THREE.NoToneMapping;
          }}
          className="touch-none"
        >
          <Atmosphere />
          <Clouds />
          <Motes />
          <Birds
            space={space}
            selectedId={selected?.id ?? null}
            highlightIds={highlightIds}
            onPick={flyTo}
            flight={flight}
            lang={lang}
            species={species}
          />
          <FlightRig keys={keys} flight={flight} />
          <Bloom />
        </Canvas>
      ) : null}

      {/* HUD — kept sparse so the sky stays the subject. */}
      <div className="pointer-events-none absolute inset-0 flex flex-col justify-between p-4 sm:p-6">
        <div className="flex items-start justify-between gap-4">
          <p className="pointer-events-auto font-mono text-[10px] uppercase tracking-[0.18em] text-[#14202c]/70">
            기예 Giye · {t("창공", "Sky")}
          </p>
          <div className="pointer-events-auto flex shrink-0 flex-col items-end gap-1 whitespace-nowrap text-right font-mono text-[10px] text-[#14202c]/60">
            <button
              type="button"
              onClick={() => setLang(lang === "ko" ? "en" : "ko")}
              className="mb-1 border border-[#14202c]/30 px-2 py-0.5 text-[10px] text-[#14202c]/80 transition hover:border-[#14202c] hover:text-[#14202c]"
              aria-label={t("언어 전환", "Switch language")}
            >
              {lang === "ko" ? "EN" : "한국어"}
            </button>
            <span>
              {t("고도", "ALT")} {hud.altitude}
              {" · "}
              {t("속도", "SPD")} {hud.speed}
            </span>
            <button
              type="button"
              onClick={() => void toggleWind()}
              aria-pressed={windOn}
              className="underline-offset-2 hover:text-[#14202c] hover:underline"
            >
              {windOn ? t("바람 소리 끄기", "Wind off") : t("바람 소리 켜기", "Wind on")}
            </button>
          </div>
        </div>

        <div className="flex flex-col gap-4">
          <div className="flex flex-wrap items-end justify-between gap-4">
            <div className="pointer-events-auto max-w-sm">
              {selected && (hud.perched || !hud.flying) ? (
                <div className="rounded-2xl border border-white/60 bg-white/55 px-5 py-4 text-[#14202c] shadow-[0_8px_30px_-12px_rgba(20,32,44,0.25)] backdrop-blur-md">
                  <p className="font-mono text-[10px] uppercase tracking-wider text-[#14202c]/50">
                    {selected.id}
                  </p>
                  <h2 className="mt-0.5 font-display text-xl leading-tight">{name(selected)}</h2>
                  <p className="mt-1 text-xs text-[#14202c]/70">
                    {[...selected.medium_tags, ...selected.regions].slice(0, 5).join(" · ") ||
                      t("태그 없음", "No tags yet")}
                  </p>
                  <div className="mt-3 flex flex-wrap items-center gap-3 text-sm">
                    <Link
                      to="/artist/$id"
                      params={{ id: selected.id }}
                      className="rounded-full border border-[#14202c]/30 bg-[#14202c]/85 px-3.5 py-1 font-mono text-xs text-[#f5f0e8] no-underline transition hover:bg-[#14202c]"
                    >
                      {t("기록 보기", "Open record")}
                    </Link>
                    <button
                      type="button"
                      onClick={() => {
                        flight.resumeRequested = true;
                      }}
                      className="font-mono text-xs text-[#14202c]/70 underline-offset-2 hover:underline"
                    >
                      {t("다시 날기", "Take off")}
                    </button>
                  </div>
                </div>
              ) : selected ? (
                <p className="font-mono text-xs text-[#14202c]/70">
                  {t("활강 중 → ", "Gliding to → ")}
                  {name(selected)}
                </p>
              ) : null}
            </div>

            <div className="pointer-events-auto flex flex-col items-end gap-1 text-right font-mono text-[10px] text-[#14202c]/55">
              <p
                className={`transition-opacity duration-700 ${showHint ? "opacity-100" : "opacity-0 hover:opacity-100"}`}
              >
                {t(
                  "드래그 방향 · Space 날갯짓 · W/S 속도 · C 부르기 · 새를 눌러 곁으로 · / 안내자",
                  "Drag to steer · Space to flap · W/S speed · C to call · tap a bird to join it · / guide",
                )}
              </p>
              <nav
                aria-label={t("사이트 메뉴", "Site navigation")}
                className="flex flex-wrap justify-end gap-x-3 gap-y-1"
              >
                {SITE_NAV.map((n) => (
                  <Link
                    key={n.to}
                    to={n.to}
                    className="text-[#14202c]/70 no-underline underline-offset-2 hover:text-[#14202c] hover:underline"
                  >
                    {t(n.ko, n.en)}
                  </Link>
                ))}
              </nav>
            </div>
          </div>

          {/* Guide bar — bottom centre. Ask where to fly; the guide answers and steers. */}
          <div className="pointer-events-auto mx-auto w-full max-w-xl">
            {log.length > 0 && (
              <ol className="mb-2 space-y-1.5" aria-live="polite">
                {log.map((m) => (
                  <li
                    key={m.id}
                    className={`text-xs leading-5 ${m.role === "you" ? "text-right text-[#14202c]/60" : "text-[#14202c]"}`}
                  >
                    {m.role === "guide" ? (
                      <span className="mr-1.5 font-mono text-[10px] uppercase tracking-wider text-[#14202c]/45">
                        {t("안내자", "Guide")}
                      </span>
                    ) : null}
                    {m.text}
                    {m.chips && m.chips.length > 0 ? (
                      <span className="ml-1 inline-flex flex-wrap gap-x-2">
                        {m.chips.map((c) => (
                          <button
                            key={c.id}
                            type="button"
                            onClick={() => flyTo(c)}
                            className="underline underline-offset-2 hover:text-[#E39A3B]"
                          >
                            {name(c)}
                          </button>
                        ))}
                      </span>
                    ) : null}
                  </li>
                ))}
              </ol>
            )}
            <form
              onSubmit={onSearch}
              className="flex items-center gap-2 rounded-full border border-white/60 bg-white/55 py-1.5 pl-1.5 pr-2 shadow-[0_8px_30px_-12px_rgba(20,32,44,0.25)] backdrop-blur-md"
            >
              <button
                type="button"
                onClick={doCall}
                aria-label={t("부르기", "Call")}
                title={t("부르기 (C)", "Call (C)")}
                className="group grid size-10 shrink-0 place-items-center rounded-full bg-[#14202c]/6 transition hover:bg-[#E39A3B]/25"
              >
                <span className="block size-4 rounded-full border-2 border-[#14202c]/70 transition group-hover:scale-125 group-hover:border-[#c8781c]" />
              </button>
              <label htmlFor="sky-q" className="sr-only">
                {t("안내자에게 묻기", "Ask the guide")}
              </label>
              <input
                id="sky-q"
                value={query}
                onChange={(e) => setQuery(e.target.value)}
                placeholder={t(
                  "어디로 날아갈까요? 이름·매체·지역을 말해 보세요",
                  "Where to? Say a name, a medium or a region",
                )}
                autoComplete="off"
                className="min-w-0 flex-1 bg-transparent px-1 py-1 text-sm text-[#14202c] placeholder:text-[#14202c]/40 outline-none"
              />
              <button
                type="submit"
                className="rounded-full border border-[#14202c]/30 bg-[#14202c]/85 px-3.5 py-1.5 font-mono text-xs text-[#f5f0e8] transition hover:bg-[#14202c]"
              >
                {t("날아가기", "Fly")}
              </button>
            </form>
          </div>
        </div>
      </div>
    </div>
  );
}
