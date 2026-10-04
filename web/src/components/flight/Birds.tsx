// SPDX-License-Identifier: AGPL-3.0-only
import { useFrame, useThree } from "@react-three/fiber";
import { useEffect, useMemo, useRef } from "react";
import * as THREE from "three";
import type { EmbeddingPoint, EmbeddingSpace } from "@/lib/flightSearch";
import type { BirdSpecies } from "./birdModels";
import { clamp, damp, mulberry32, smoothstep, type FlightState } from "./flightState";
import { playChirps, playFlap } from "./sound";
import { makeLabelTexture, makeRadialTexture } from "./textures";

/* ------------------------------------------------------------------ */
/* Render backends: glTF morph-target models, or a procedural fallback  */
/* ------------------------------------------------------------------ */

type Backend = {
  objects: THREE.Object3D[];
  set: (
    i: number,
    matrix: THREE.Matrix4,
    color: THREE.Color,
    dt: number,
    flapRate: number,
    glide: boolean,
  ) => void;
  commit: (time: number, fog: THREE.Fog | null) => void;
  scaleOf: (i: number) => number;
  dispose: () => void;
};

/** Bird silhouette, forward = +Z, wingspan = 2 units (±1 on x). */
function makeBirdGeometry(): THREE.BufferGeometry {
  const pos: number[] = [];
  const wing: number[] = [];
  const tri = (
    a: [number, number, number],
    b: [number, number, number],
    c: [number, number, number],
    wa: number,
    wb: number,
    wc: number,
  ) => {
    pos.push(...a, ...b, ...c);
    wing.push(wa, wb, wc);
  };
  const nose: [number, number, number] = [0, 0, 0.58];
  const tail: [number, number, number] = [0, 0, -0.46];
  const l: [number, number, number] = [-0.085, 0, 0.02];
  const r: [number, number, number] = [0.085, 0, 0.02];
  const top: [number, number, number] = [0, 0.05, 0.0];
  tri(nose, l, top, 0, 0, 0);
  tri(nose, top, r, 0, 0, 0);
  tri(tail, top, l, 0, 0, 0);
  tri(tail, r, top, 0, 0, 0);
  tri(tail, [-0.14, 0, -0.72], [0, 0, -0.54], 0, 0, 0);
  tri(tail, [0, 0, -0.54], [0.14, 0, -0.72], 0, 0, 0);
  const stations: Array<[number, number, number]> = [
    [0.08, 0.26, -0.12],
    [0.42, 0.22, -0.15],
    [0.74, 0.08, -0.23],
    [1.0, -0.14, -0.3],
  ];
  for (const s of [1, -1] as const) {
    for (let k = 0; k < stations.length - 1; k++) {
      const [x0, f0, b0] = stations[k]!;
      const [x1, f1, b1] = stations[k + 1]!;
      const F0: [number, number, number] = [s * x0, 0, f0];
      const B0: [number, number, number] = [s * x0, 0, b0];
      const F1: [number, number, number] = [s * x1, 0, f1];
      const B1: [number, number, number] = [s * x1, 0, b1];
      tri(F0, F1, B0, s * x0, s * x1, s * x0);
      tri(B0, F1, B1, s * x0, s * x1, s * x1);
    }
  }
  const g = new THREE.BufferGeometry();
  g.setAttribute("position", new THREE.Float32BufferAttribute(pos, 3));
  g.setAttribute("aWing", new THREE.Float32BufferAttribute(wing, 1));
  g.computeBoundingSphere();
  return g;
}

const VERT = /* glsl */ `
  attribute float aWing;
  attribute float aPhase;
  attribute float aFlap;
  varying vec3 vColor;
  varying float vSpan;
  varying float vShade;
  varying float vDepth;
  void main() {
    #ifdef USE_INSTANCING_COLOR
      vColor = instanceColor;
    #else
      vColor = vec3(0.12);
    #endif
    float w = abs(aWing);
    float beat = sin(aPhase - w * 1.25) * 0.6 * aFlap;
    float ang = beat * (0.3 + 0.7 * w);
    vec3 p = position;
    p.x = position.x * cos(ang);
    p.y = position.y + w * sin(ang);
    vSpan = w;
    vShade = 0.5 + 0.5 * cos(beat * 1.5);
    vec4 world = modelMatrix * instanceMatrix * vec4(p, 1.0);
    vec4 mv = viewMatrix * world;
    vDepth = -mv.z;
    gl_Position = projectionMatrix * mv;
  }
`;

const FRAG = /* glsl */ `
  uniform vec3 uFogColor;
  uniform float uFogNear;
  uniform float uFogFar;
  varying vec3 vColor;
  varying float vSpan;
  varying float vShade;
  varying float vDepth;
  void main() {
    vec3 col = mix(vColor, vColor * 1.75 + vec3(0.13, 0.12, 0.10), vSpan * 0.62);
    col *= 0.76 + 0.34 * vShade;
    float f = smoothstep(uFogNear, uFogFar, vDepth);
    col = mix(col, uFogColor, f);
    gl_FragColor = vec4(col, 1.0 - f * 0.9);
    #include <colorspace_fragment>
  }
`;

function makeProceduralBackend(n: number): Backend {
  const geometry = makeBirdGeometry();
  const rnd = mulberry32(4242);
  const phase = new Float32Array(n);
  const flap = new Float32Array(n);
  for (let i = 0; i < n; i++) {
    phase[i] = rnd() * Math.PI * 2;
    flap[i] = 1;
  }
  const phaseAttr = new THREE.InstancedBufferAttribute(phase, 1);
  const flapAttr = new THREE.InstancedBufferAttribute(flap, 1);
  geometry.setAttribute("aPhase", phaseAttr);
  geometry.setAttribute("aFlap", flapAttr);
  const material = new THREE.ShaderMaterial({
    vertexShader: VERT,
    fragmentShader: FRAG,
    side: THREE.DoubleSide,
    transparent: true,
    uniforms: {
      uFogColor: { value: new THREE.Color("#c5dbec") },
      uFogNear: { value: 40 },
      uFogFar: { value: 140 },
    },
  });
  const mesh = new THREE.InstancedMesh(geometry, material, n);
  mesh.frustumCulled = false;
  const c = new THREE.Color("#1a1a1a");
  for (let i = 0; i < n; i++) mesh.setColorAt(i, c);
  return {
    objects: [mesh],
    set(i, matrix, color, dt, flapRate, glide) {
      mesh.setMatrixAt(i, matrix);
      mesh.setColorAt(i, color);
      phase[i] = (phase[i]! + dt * flapRate * 5.4) % (Math.PI * 2);
      flap[i] = damp(flap[i]!, glide ? 0.08 : 1, 3, dt);
    },
    commit(_time, fog) {
      phaseAttr.needsUpdate = true;
      flapAttr.needsUpdate = true;
      mesh.instanceMatrix.needsUpdate = true;
      if (mesh.instanceColor) mesh.instanceColor.needsUpdate = true;
      if (fog) {
        (material.uniforms.uFogColor!.value as THREE.Color).copy(fog.color);
        material.uniforms.uFogNear!.value = fog.near;
        material.uniforms.uFogFar!.value = fog.far;
      }
    },
    scaleOf: () => 1,
    dispose() {
      geometry.dispose();
      material.dispose();
    },
  };
}

/**
 * Luminous bird material: pearl body lit by the sun, a warm fresnel rim so
 * edges glow against the sky (light creatures, not silhouettes).
 */
function makeLuminousMaterial(): THREE.MeshStandardMaterial {
  const m = new THREE.MeshStandardMaterial({
    vertexColors: true,
    roughness: 0.82,
    metalness: 0,
    emissive: new THREE.Color("#ffd8ad"),
    emissiveIntensity: 0.14,
  });
  m.onBeforeCompile = (shader) => {
    shader.uniforms.uRim = { value: new THREE.Color("#ffe4bd") };
    shader.fragmentShader = shader.fragmentShader
      .replace("#include <common>", "#include <common>\nuniform vec3 uRim;")
      .replace(
        "#include <emissivemap_fragment>",
        `#include <emissivemap_fragment>
        {
          vec3 nrm = normalize( normal );
          vec3 vdir = normalize( vViewPosition );
          float fres = pow( 1.0 - saturate( abs( dot( nrm, vdir ) ) ), 2.2 );
          totalEmissiveRadiance += uRim * fres * 0.9;
        }`,
      );
  };
  return m;
}

function makeModelBackend(species: BirdSpecies[], n: number, speciesOf: Int16Array): Backend {
  const S = species.length;
  const material = makeLuminousMaterial();
  const localOf = new Int32Array(n);
  const counts = new Int32Array(S);
  for (let i = 0; i < n; i++) {
    const s = speciesOf[i]!;
    localOf[i] = counts[s]!;
    counts[s]! += 1;
  }
  const meshes: THREE.InstancedMesh[] = [];
  const mixers: THREE.AnimationMixer[] = [];
  const rnd = mulberry32(31337);
  const animT = new Float32Array(n);
  for (let i = 0; i < n; i++) animT[i] = rnd() * 10;
  species.forEach((sp, s) => {
    const mesh = new THREE.InstancedMesh(sp.geometry, material, Math.max(1, counts[s]!));
    mesh.frustumCulled = false;
    const c = new THREE.Color("#f2ebe0");
    for (let j = 0; j < mesh.count; j++) mesh.setColorAt(j, c);
    meshes.push(mesh);
    const mixer = new THREE.AnimationMixer(sp.root);
    mixer.clipAction(sp.clip).play();
    mixers.push(mixer);
  });
  return {
    objects: meshes,
    set(i, matrix, color, dt, flapRate, glide) {
      const s = speciesOf[i]!;
      const j = localOf[i]!;
      const mesh = meshes[s]!;
      const sp = species[s]!;
      mesh.setMatrixAt(j, matrix);
      mesh.setColorAt(j, color);
      const cur = animT[i]!;
      let next = cur;
      if (glide) {
        let gap = sp.glideTime - cur;
        if (gap < 0) gap += sp.duration;
        const step = dt * Math.max(flapRate, 0.6);
        next = gap <= step ? sp.glideTime : (cur + step) % sp.duration;
      } else {
        next = (cur + dt * flapRate) % sp.duration;
      }
      animT[i] = next;
      mixers[s]!.setTime(next);
      mesh.setMorphAt(j, sp.dummy);
    },
    commit() {
      for (const mesh of meshes) {
        mesh.instanceMatrix.needsUpdate = true;
        if (mesh.instanceColor) mesh.instanceColor.needsUpdate = true;
        if (mesh.morphTexture) mesh.morphTexture.needsUpdate = true;
      }
    },
    scaleOf: (i) => species[speciesOf[i]!]!.scale,
    dispose() {
      material.dispose();
      for (const m of meshes) m.dispose();
    },
  };
}

/* ------------------------------------------------------------------ */
/* Flock choreography                                                    */
/* ------------------------------------------------------------------ */

/** Pearl bodies with a pastel hue per flock; the rim light does the rest. */
const FLOCK_TINTS = [
  "#F7DFC6",
  "#CFDFF4",
  "#E3D3F0",
  "#D3E8D3",
  "#F4D3D2",
  "#DDDCE8",
  "#F1E4C4",
  "#CBE3E6",
];
const INK_TINTS = [
  "#2c3e50",
  "#2c4a3e",
  "#5c3317",
  "#1e3a5f",
  "#4a3728",
  "#3d2b4a",
  "#2f4f4f",
  "#4a2020",
];
const WARM = new THREE.Color("#FFC46B").multiplyScalar(1.25);
const LIGHT = new THREE.Color("#FFF6E6").multiplyScalar(1.2);

const MAX_TURN = 1.15; // rad/s
const MAX_ACC = 5.5;
const SEP_R = 1.35;
const SPARKS = 900;
const LABELS = 4;

type Props = {
  space: EmbeddingSpace;
  selectedId: string | null;
  highlightIds: Set<string>;
  onPick: (p: EmbeddingPoint) => void;
  flight: FlightState;
  lang: "ko" | "en";
  species: BirdSpecies[];
};

export function Birds({ space, selectedId, highlightIds, onPick, flight, lang, species }: Props) {
  const { camera, gl, scene, size } = useThree();
  const n = space.points.length;
  const lit = species.length > 0;

  const flock = useMemo(() => {
    const rnd = mulberry32(777);
    const clusterIds = Array.from(new Set(space.points.map((p) => p.cluster))).sort(
      (a, b) => a - b,
    );
    const slotOf = new Map<number, number>();
    clusterIds.forEach((id, k) => slotOf.set(id, k));
    const C = clusterIds.length;

    const slot = new Int16Array(n);
    const count = new Int32Array(C);
    const centroid = new Float32Array(C * 3);
    for (let i = 0; i < n; i++) {
      const p = space.points[i]!;
      const k = slotOf.get(p.cluster) ?? 0;
      slot[i] = k;
      count[k]! += 1;
      centroid[k * 3] += p.x;
      centroid[k * 3 + 1] += p.y;
      centroid[k * 3 + 2] += p.z;
    }
    for (let k = 0; k < C; k++) {
      const c = Math.max(1, count[k]!);
      centroid[k * 3] = centroid[k * 3]! / c;
      centroid[k * 3 + 1] = centroid[k * 3 + 1]! / c;
      centroid[k * 3 + 2] = centroid[k * 3 + 2]! / c;
    }

    const speciesOf = new Int16Array(n);
    const speciesForSlot = new Int16Array(C);
    if (species.length > 0) {
      const bySize = [...Array(C).keys()].sort((a, b) => count[b]! - count[a]!);
      const compactFirst = [...species.keys()].sort(
        (a, b) => species[a]!.scale - species[b]!.scale,
      );
      bySize.forEach((k, rank) => {
        speciesForSlot[k] = compactFirst[rank % species.length]!;
      });
      for (let i = 0; i < n; i++) speciesOf[i] = speciesForSlot[slot[i]!]!;
    }

    const radius = new Float32Array(C);
    const omega = new Float32Array(C);
    const phase = new Float32Array(C);
    const tilt = new Float32Array(C);
    const kind = new Uint8Array(C);
    for (let k = 0; k < C; k++) {
      radius[k] = 8 + rnd() * 5;
      const v = 1.7 + rnd() * 0.5;
      omega[k] = (v / radius[k]!) * (rnd() < 0.5 ? 1 : -1);
      phase[k] = rnd() * Math.PI * 2;
      tilt[k] = (rnd() - 0.5) * 0.35;
      const largeBird =
        species.length > 0 ? species[speciesForSlot[k]!]!.scale >= 0.02 : count[k]! >= 6;
      kind[k] = largeBird ? 1 : 0;
    }

    const offset = new Float32Array(n * 3);
    const memberIdx = new Int32Array(n);
    const seen = new Int32Array(C);
    for (let i = 0; i < n; i++) {
      const k = slot[i]!;
      memberIdx[i] = seen[k]!;
      seen[k]! += 1;
    }
    for (let i = 0; i < n; i++) {
      const k = slot[i]!;
      const j = memberIdx[i]!;
      const m = count[k]!;
      if (kind[k] === 1) {
        const g = Math.floor(j / 9);
        const jj = j % 9;
        const rank = Math.ceil(jj / 2);
        const side = jj % 2 === 1 ? -1 : 1;
        const gx = (g % 2 === 1 ? -1 : 1) * Math.ceil(g / 2) * 5.5;
        const gz = -g * 4.2;
        offset[i * 3] = gx + side * rank * (1.35 + rnd() * 0.15);
        offset[i * 3 + 1] = -rank * 0.05 + (rnd() - 0.5) * 0.25 + g * 0.3;
        offset[i * 3 + 2] = gz - rank * (1.45 + rnd() * 0.1);
      } else {
        const rx = 1.6 + 0.42 * Math.sqrt(m);
        const u = rnd() * Math.PI * 2;
        const w = Math.acos(2 * rnd() - 1);
        const rr = Math.cbrt(rnd()) * rx;
        offset[i * 3] = Math.sin(w) * Math.cos(u) * rr;
        offset[i * 3 + 1] = Math.cos(w) * rr * 0.38;
        offset[i * 3 + 2] = Math.sin(w) * Math.sin(u) * rr;
      }
    }

    const pos = new Float32Array(n * 3);
    const vel = new Float32Array(n * 3);
    const noise = new Float32Array(n * 6);
    const baseScale = new Float32Array(n);
    const flapBase = new Float32Array(n);
    const glideT = new Float32Array(n);
    const gliding = new Uint8Array(n);
    const curRoll = new Float32Array(n);
    for (let i = 0; i < n; i++) {
      const k = slot[i]!;
      const th = phase[k]!;
      const cx = centroid[k * 3]! + Math.cos(th) * radius[k]!;
      const cz = centroid[k * 3 + 2]! + Math.sin(th) * radius[k]!;
      pos[i * 3] = cx + offset[i * 3]! + (rnd() - 0.5);
      pos[i * 3 + 1] = centroid[k * 3 + 1]! + offset[i * 3 + 1]!;
      pos[i * 3 + 2] = cz + offset[i * 3 + 2]! + (rnd() - 0.5);
      const dir = Math.sign(omega[k]!);
      vel[i * 3] = -Math.sin(th) * dir * 1.8;
      vel[i * 3 + 2] = Math.cos(th) * dir * 1.8;
      for (let q = 0; q < 6; q++) noise[i * 6 + q] = rnd() * Math.PI * 2;
      baseScale[i] = 0.62 + rnd() * 0.26;
      flapBase[i] = 0.85 + rnd() * 0.3;
      glideT[i] = 2 + rnd() * 6;
    }

    const tints = lit ? FLOCK_TINTS : INK_TINTS;
    const baseColor = clusterIds.map((_, k) => new THREE.Color(tints[k % tints.length]!));
    const curColor = new Float32Array(n * 3);
    for (let i = 0; i < n; i++) {
      const c = baseColor[slot[i]!]!;
      curColor[i * 3] = c.r;
      curColor[i * 3 + 1] = c.g;
      curColor[i * 3 + 2] = c.b;
    }

    return {
      C,
      clusterIds,
      slot,
      count,
      centroid,
      speciesOf,
      radius,
      omega,
      phase,
      tilt,
      kind,
      offset,
      pos,
      vel,
      noise,
      baseScale,
      flapBase,
      glideT,
      gliding,
      curRoll,
      baseColor,
      curColor,
      curScale: Float32Array.from(baseScale),
      center: new Float32Array(C * 3),
      centerVel: new Float32Array(C * 3),
      // Greeting visits after a call, and the glow that ripples out with the ring.
      visitStart: new Float32Array(n),
      visitEnd: new Float32Array(n),
      visitPhase: new Float32Array(n),
      visitR: new Float32Array(n),
      visitDir: new Float32Array(n),
      glow: new Float32Array(n),
    };
  }, [space.points, n, species, lit]);

  const backend = useMemo<Backend>(
    () => (lit ? makeModelBackend(species, n, flock.speciesOf) : makeProceduralBackend(n)),
    [species, n, flock.speciesOf, lit],
  );
  useEffect(() => () => backend.dispose(), [backend]);

  useEffect(() => {
    flight.live = flock.pos;
    flight.liveVel = flock.vel;
    flight.flockCenters = flock.center;
    return () => {
      flight.live = null;
      flight.liveVel = null;
      flight.flockCenters = null;
    };
  }, [flight, flock]);

  const selectedIndex = useMemo(
    () => (selectedId ? space.points.findIndex((p) => p.id === selectedId) : -1),
    [space.points, selectedId],
  );
  const highlightIdx = useMemo(() => {
    const s = new Set<number>();
    space.points.forEach((p, i) => {
      if (highlightIds.has(p.id)) s.add(i);
    });
    return s;
  }, [space.points, highlightIds]);

  /* ---- selection halo, landing ring, call ring, trail ---- */
  const halo = useRef<THREE.Sprite>(null);
  const ring = useRef<THREE.Sprite>(null);
  const callRing = useRef<THREE.Sprite>(null);
  const haloTex = useMemo(
    () =>
      makeRadialTexture([
        [0, "rgba(255,214,140,0.9)"],
        [0.25, "rgba(255,200,120,0.35)"],
        [0.6, "rgba(255,190,110,0.08)"],
        [1, "rgba(255,190,110,0)"],
      ]),
    [],
  );
  const ringTex = useMemo(
    () =>
      makeRadialTexture(
        [
          [0, "rgba(255,255,255,0)"],
          [0.78, "rgba(255,255,255,0)"],
          [0.86, "rgba(255,225,170,0.85)"],
          [0.93, "rgba(255,225,170,0.2)"],
          [1, "rgba(255,225,170,0)"],
        ],
        256,
      ),
    [],
  );
  const sparkTex = useMemo(
    () =>
      makeRadialTexture(
        [
          [0, "rgba(255,255,255,1)"],
          [0.3, "rgba(255,250,240,0.7)"],
          [1, "rgba(255,240,220,0)"],
        ],
        64,
      ),
    [],
  );

  const TRAIL = 42;
  const trail = useMemo(() => {
    const g = new THREE.BufferGeometry();
    g.setAttribute("position", new THREE.BufferAttribute(new Float32Array(TRAIL * 3), 3));
    g.setAttribute("color", new THREE.BufferAttribute(new Float32Array(TRAIL * 3), 3));
    const m = new THREE.LineBasicMaterial({
      vertexColors: true,
      transparent: true,
      opacity: 0.7,
      depthWrite: false,
      toneMapped: false,
    });
    const line = new THREE.Line(g, m);
    line.frustumCulled = false;
    line.visible = false;
    return { line, g, m };
  }, []);
  useEffect(
    () => () => {
      trail.g.dispose();
      trail.m.dispose();
    },
    [trail],
  );

  /* ---- sparkle motes shed by nearby birds (light creatures leave light) ---- */
  const sparks = useMemo(() => {
    const g = new THREE.BufferGeometry();
    const position = new Float32Array(SPARKS * 3);
    const color = new Float32Array(SPARKS * 3);
    const aVel = new Float32Array(SPARKS * 3);
    const aBirth = new Float32Array(SPARKS).fill(-1000);
    const aLife = new Float32Array(SPARKS).fill(1);
    const aSeed = new Float32Array(SPARKS);
    g.setAttribute("position", new THREE.BufferAttribute(position, 3));
    g.setAttribute("color", new THREE.BufferAttribute(color, 3));
    g.setAttribute("aVel", new THREE.BufferAttribute(aVel, 3));
    g.setAttribute("aBirth", new THREE.BufferAttribute(aBirth, 1));
    g.setAttribute("aLife", new THREE.BufferAttribute(aLife, 1));
    g.setAttribute("aSeed", new THREE.BufferAttribute(aSeed, 1));
    const m = new THREE.ShaderMaterial({
      transparent: true,
      depthWrite: false,
      blending: THREE.AdditiveBlending,
      vertexColors: true,
      uniforms: { uTime: { value: 0 }, uMap: { value: sparkTex } },
      vertexShader: /* glsl */ `
        attribute vec3 aVel;
        attribute float aBirth;
        attribute float aLife;
        attribute float aSeed;
        uniform float uTime;
        varying float vA;
        varying vec3 vC;
        void main() {
          float age = uTime - aBirth;
          float k = clamp(age / aLife, 0.0, 1.0);
          vA = (1.0 - k) * smoothstep(0.0, 0.12, k);
          vC = color;
          vec3 p = position + aVel * age + vec3(0.0, 0.22 * age, 0.0)
                 + vec3(sin(aSeed + age * 3.0), 0.0, cos(aSeed * 1.3 + age * 2.5)) * 0.07 * age;
          vec4 mv = modelViewMatrix * vec4(p, 1.0);
          gl_PointSize = (0.8 + 0.5 * fract(aSeed)) * (110.0 / max(1.0, -mv.z)) * (1.0 - k * 0.45);
          gl_Position = projectionMatrix * mv;
        }
      `,
      fragmentShader: /* glsl */ `
        uniform sampler2D uMap;
        varying float vA;
        varying vec3 vC;
        void main() {
          vec4 t = texture2D(uMap, gl_PointCoord);
          gl_FragColor = vec4(vC * t.rgb, t.a * vA);
          #include <colorspace_fragment>
        }
      `,
    });
    const points = new THREE.Points(g, m);
    points.frustumCulled = false;
    return { points, g, m, head: 0 };
  }, [sparkTex]);
  useEffect(
    () => () => {
      sparks.g.dispose();
      sparks.m.dispose();
    },
    [sparks],
  );

  /* ---- name labels for the closest birds (approach to learn who they are) ---- */
  const labelGroup = useRef<THREE.Group>(null);
  const labelCache = useMemo(() => new Map<string, THREE.CanvasTexture>(), []);
  useEffect(() => () => labelCache.forEach((t) => t.dispose()), [labelCache]);
  const labelFor = (i: number) => {
    const p = space.points[i]!;
    const key = `${p.id}:${lang}`;
    let tex = labelCache.get(key);
    if (!tex) {
      const nm = (lang === "ko" ? p.name_ko : p.name_en) || p.name_ko || p.name_en || p.id;
      tex = makeLabelTexture(nm, p.id);
      labelCache.set(key, tex);
    }
    return tex;
  };

  const dummy = useMemo(() => new THREE.Object3D(), []);
  const vP = useMemo(() => new THREE.Vector3(), []);
  const vT = useMemo(() => new THREE.Vector3(), []);
  const vD = useMemo(() => new THREE.Vector3(), []);
  const vOld = useMemo(() => new THREE.Vector3(), []);
  const vNew = useMemo(() => new THREE.Vector3(), []);
  const fwd = useMemo(() => new THREE.Vector3(), []);
  const right = useMemo(() => new THREE.Vector3(), []);
  const tmpC = useMemo(() => new THREE.Color(), []);
  const lastTrailIdx = useRef(-1);
  const lastCall = useRef(0);
  const near = useMemo(() => new Float32Array(n), [n]);
  const order = useMemo(() => Array.from({ length: n }, (_, i) => i), [n]);

  const flockCenter = (k: number, t: number, out: THREE.Vector3) => {
    const { centroid, radius, omega, phase, tilt } = flock;
    const th = omega[k]! * t + phase[k]!;
    const r = radius[k]!;
    const wander = Math.sin(0.07 * t + k * 1.7) * 2.5;
    out.set(
      centroid[k * 3]! + Math.cos(th) * r + wander,
      centroid[k * 3 + 1]! + Math.sin(th * 0.5 + k) * 1.2 + Math.sin(th) * r * tilt[k]!,
      centroid[k * 3 + 2]! + Math.sin(th) * r + Math.cos(0.05 * t + k) * 2.5,
    );
  };

  useFrame(({ clock }, rawDt) => {
    const dt = Math.min(rawDt, 0.05);
    const t = clock.elapsedTime;
    const now = performance.now();
    const cam = camera.position;
    const {
      C,
      slot,
      count,
      offset,
      pos,
      vel,
      noise,
      baseScale,
      flapBase,
      glideT,
      gliding,
      curRoll,
      baseColor,
      curColor,
      curScale,
      center,
      centerVel,
      kind,
      visitStart,
      visitEnd,
      visitPhase,
      visitR,
      visitDir,
      glow,
    } = flock;

    // 0) A new call: the nearest birds peel off to circle the caller.
    if (flight.callAt !== lastCall.current) {
      lastCall.current = flight.callAt;
      flight.callX = cam.x;
      flight.callY = cam.y;
      flight.callZ = cam.z;
      for (let i = 0; i < n; i++) {
        near[i] = Math.hypot(pos[i * 3]! - cam.x, pos[i * 3 + 1]! - cam.y, pos[i * 3 + 2]! - cam.z);
      }
      order.sort((a, b) => near[a]! - near[b]!);
      let picked = 0;
      for (let q = 0; q < n && picked < 6; q++) {
        const i = order[q]!;
        if (near[i]! > 45) break;
        visitStart[i] = now;
        visitEnd[i] = now + 9000 + Math.random() * 4000;
        visitPhase[i] = Math.random() * Math.PI * 2;
        visitR[i] = 3.8 + Math.random() * 2.2;
        visitDir[i] = picked % 2 === 0 ? 1 : -1;
        picked++;
      }
      if (picked > 0) playChirps(picked);
    }
    const callAge = flight.callAt > 0 ? (now - flight.callAt) / 1000 : 99;
    const ringR = callAge * 16;

    // 1) Flock frames.
    for (let k = 0; k < C; k++) {
      flockCenter(k, t, vP);
      flockCenter(k, t + 0.05, vT);
      vT.sub(vP).multiplyScalar(20);
      center[k * 3] = vP.x;
      center[k * 3 + 1] = vP.y;
      center[k * 3 + 2] = vP.z;
      centerVel[k * 3] = vT.x;
      centerVel[k * 3 + 1] = vT.y;
      centerVel[k * 3 + 2] = vT.z;
    }

    const sparkPos = sparks.g.getAttribute("position") as THREE.BufferAttribute;
    const sparkCol = sparks.g.getAttribute("color") as THREE.BufferAttribute;
    const sparkVel = sparks.g.getAttribute("aVel") as THREE.BufferAttribute;
    const sparkBirth = sparks.g.getAttribute("aBirth") as THREE.BufferAttribute;
    const sparkLife = sparks.g.getAttribute("aLife") as THREE.BufferAttribute;
    const sparkSeed = sparks.g.getAttribute("aSeed") as THREE.BufferAttribute;
    let spawned = false;

    // 2) Birds.
    for (let i = 0; i < n; i++) {
      const ix = i * 3;
      const k = slot[i]!;
      fwd.set(centerVel[k * 3]!, 0, centerVel[k * 3 + 2]!);
      let flockSpeed =
        Math.hypot(centerVel[k * 3]!, centerVel[k * 3 + 1]!, centerVel[k * 3 + 2]!) || 1.8;
      if (fwd.lengthSq() < 1e-6) fwd.set(0, 0, 1);
      fwd.normalize();
      right.set(fwd.z, 0, -fwd.x);
      const nq = i * 6;
      const amp = kind[k] === 1 ? 0.28 : 0.7;
      const nx =
        Math.sin(t * 0.23 + noise[nq]!) * amp + Math.sin(t * 0.41 + noise[nq + 1]!) * amp * 0.5;
      const ny = Math.sin(t * 0.19 + noise[nq + 2]!) * amp * 0.6;
      const nz = Math.sin(t * 0.27 + noise[nq + 3]!) * amp;
      const ox = offset[ix]! + nx;
      const oy = offset[ix + 1]! + ny;
      const oz = offset[ix + 2]! + nz;
      vT.set(
        center[k * 3]! + right.x * ox + fwd.x * oz,
        center[k * 3 + 1]! + oy,
        center[k * 3 + 2]! + right.z * ox + fwd.z * oz,
      );

      // Greeting: blend the slot toward a circle around the viewer.
      let visiting = 0;
      if (now < visitEnd[i]!) {
        const dur = (visitEnd[i]! - visitStart[i]!) / 1000;
        const age = (now - visitStart[i]!) / 1000;
        visiting = smoothstep(0, 1.4, age) * (1 - smoothstep(dur - 1.6, dur, age));
        const a = visitPhase[i]! + t * 0.7 * visitDir[i]!;
        const R = visitR[i]!;
        vD.set(
          cam.x + Math.cos(a) * R,
          cam.y + 0.4 + Math.sin(a * 0.7) * 0.6,
          cam.z + Math.sin(a) * R,
        );
        vT.lerp(vD, visiting);
        flockSpeed = flockSpeed * (1 - visiting) + 4.6 * visiting;
      }

      vD.set(vT.x - pos[ix]!, vT.y - pos[ix + 1]!, vT.z - pos[ix + 2]!);
      const gap = vD.length();
      vD.multiplyScalar(gap > 0 ? clamp(gap * 0.9, 0, flockSpeed * 1.2) / gap : 0);
      vD.x += centerVel[k * 3]! * (1 - visiting);
      vD.y += centerVel[k * 3 + 1]! * (1 - visiting);
      vD.z += centerVel[k * 3 + 2]! * (1 - visiting);
      const dl = vD.length() || 1;
      const sMin = flockSpeed * 0.55;
      const sMax = flockSpeed * 1.65;
      if (dl < sMin) vD.multiplyScalar(sMin / dl);
      else if (dl > sMax) vD.multiplyScalar(sMax / dl);

      let ax = (vD.x - vel[ix]!) * 2.6;
      let ay = (vD.y - vel[ix + 1]!) * 2.6;
      let az = (vD.z - vel[ix + 2]!) * 2.6;
      for (let j = 0; j < n; j++) {
        if (j === i) continue;
        const dx = pos[ix]! - pos[j * 3]!;
        const dy = pos[ix + 1]! - pos[j * 3 + 1]!;
        const dz = pos[ix + 2]! - pos[j * 3 + 2]!;
        const d2 = dx * dx + dy * dy + dz * dz;
        if (d2 < SEP_R * SEP_R && d2 > 1e-6) {
          const d = Math.sqrt(d2);
          const push = ((SEP_R - d) / SEP_R) * 4.5;
          ax += (dx / d) * push;
          ay += (dy / d) * push * 0.6;
          az += (dz / d) * push;
        }
      }
      // Never fly through the viewer.
      {
        const dx = pos[ix]! - cam.x;
        const dy = pos[ix + 1]! - cam.y;
        const dz = pos[ix + 2]! - cam.z;
        const d2 = dx * dx + dy * dy + dz * dz;
        if (d2 < 9 && d2 > 1e-6) {
          const d = Math.sqrt(d2);
          const push = ((3 - d) / 3) * 8;
          ax += (dx / d) * push;
          ay += (dy / d) * push;
          az += (dz / d) * push;
        }
      }
      const al = Math.hypot(ax, ay, az);
      if (al > MAX_ACC) {
        const s = MAX_ACC / al;
        ax *= s;
        ay *= s;
        az *= s;
      }

      vOld.set(vel[ix]!, vel[ix + 1]!, vel[ix + 2]!);
      vNew.set(vOld.x + ax * dt, vOld.y + ay * dt, vOld.z + az * dt);
      const speed = vNew.length() || 1e-4;
      const oldLen = vOld.length() || 1e-4;
      const cosA = clamp(vOld.dot(vNew) / (oldLen * speed), -1, 1);
      const ang = Math.acos(cosA);
      const lim = MAX_TURN * (1 + visiting * 0.6) * dt;
      if (ang > lim && ang > 1e-4) {
        const f = lim / ang;
        vD.copy(vOld)
          .multiplyScalar((1 - f) / oldLen)
          .addScaledVector(vNew, f / speed)
          .normalize();
        vNew.copy(vD).multiplyScalar(speed);
      }
      const turnSign = (vOld.x * vNew.z - vOld.z * vNew.x) / (oldLen * speed);
      vel[ix] = vNew.x;
      vel[ix + 1] = vNew.y;
      vel[ix + 2] = vNew.z;
      pos[ix] = pos[ix]! + vNew.x * dt;
      pos[ix + 1] = pos[ix + 1]! + vNew.y * dt;
      pos[ix + 2] = pos[ix + 2]! + vNew.z * dt;

      const yawRate = Math.asin(clamp(turnSign, -1, 1)) / Math.max(dt, 1e-3);
      curRoll[i] = damp(curRoll[i]!, clamp(-yawRate * 0.9, -0.8, 0.8), 3.5, dt);
      vP.set(pos[ix]!, pos[ix + 1]!, pos[ix + 2]!);
      dummy.position.copy(vP);
      dummy.lookAt(vP.x + vNew.x, vP.y + vNew.y, vP.z + vNew.z);
      dummy.rotateZ(curRoll[i]!);
      const sel = i === selectedIndex;
      const hi = highlightIdx.has(i);
      const targetScale = baseScale[i]! * (sel ? 1.6 : hi ? 1.25 : 1);
      curScale[i] = damp(curScale[i]!, targetScale, 6, dt);
      dummy.scale.setScalar(curScale[i]! * backend.scaleOf(i));
      dummy.updateMatrix();

      glideT[i] = glideT[i]! - dt;
      if (glideT[i]! <= 0) {
        gliding[i] = gliding[i] ? 0 : 1;
        glideT[i] = gliding[i] ? 2.5 + Math.random() * 4 : 3 + Math.random() * 5;
      }
      const climbing = vNew.y > 0.35 || gap > 2.5;
      const glide = gliding[i] === 1 && !climbing && visiting < 0.5;
      const flapRate =
        flapBase[i]! * (0.75 + clamp((speed - flockSpeed) * 0.35 + vNew.y * 0.4, 0, 0.7));

      // Ring of light passing by makes a bird flare; the flare fades.
      const dCall = Math.hypot(
        pos[ix]! - flight.callX,
        pos[ix + 1]! - flight.callY,
        pos[ix + 2]! - flight.callZ,
      );
      if (callAge < 3 && Math.abs(dCall - ringR) < 2.5) glow[i] = 1;
      glow[i] = glow[i]! * Math.exp(-1.1 * dt);
      const g = Math.max(glow[i]!, visiting * 0.7);

      const base = baseColor[k]!;
      const tr = sel ? WARM.r : hi ? LIGHT.r : base.r * (1 + g * 0.55);
      const tg = sel ? WARM.g : hi ? LIGHT.g : base.g * (1 + g * 0.5);
      const tb = sel ? WARM.b : hi ? LIGHT.b : base.b * (1 + g * 0.4);
      const kc = 1 - Math.exp(-5 * dt);
      curColor[ix] = curColor[ix]! + (tr - curColor[ix]!) * kc;
      curColor[ix + 1] = curColor[ix + 1]! + (tg - curColor[ix + 1]!) * kc;
      curColor[ix + 2] = curColor[ix + 2]! + (tb - curColor[ix + 2]!) * kc;
      tmpC.setRGB(curColor[ix]!, curColor[ix + 1]!, curColor[ix + 2]!);
      backend.set(i, dummy.matrix, tmpC, dt, flapRate, glide);

      // Shed light when near the viewer, more when glowing or greeting.
      const dCam = Math.hypot(pos[ix]! - cam.x, pos[ix + 1]! - cam.y, pos[ix + 2]! - cam.z);
      near[i] = dCam;
      if (dCam < 18) {
        const rate = (sel ? 14 : 3.5) * (1 + g * 3) * (1 - dCam / 18);
        if (Math.random() < rate * dt) {
          const h = sparks.head;
          sparks.head = (h + 1) % SPARKS;
          const jitter = 0.18;
          sparkPos.setXYZ(
            h,
            pos[ix]! - vNew.x * 0.12 + (Math.random() - 0.5) * jitter,
            pos[ix + 1]! + (Math.random() - 0.5) * jitter,
            pos[ix + 2]! - vNew.z * 0.12 + (Math.random() - 0.5) * jitter,
          );
          sparkVel.setXYZ(
            h,
            -vNew.x * 0.12 + (Math.random() - 0.5) * 0.25,
            (Math.random() - 0.5) * 0.2,
            -vNew.z * 0.12 + (Math.random() - 0.5) * 0.25,
          );
          sparkCol.setXYZ(
            h,
            Math.min(1.4, tmpC.r * 1.15 + 0.1),
            Math.min(1.3, tmpC.g * 1.1 + 0.05),
            tmpC.b,
          );
          sparkBirth.setX(h, t);
          sparkLife.setX(h, 1.1 + Math.random() * 1.1);
          sparkSeed.setX(h, Math.random() * 100);
          spawned = true;
        }
      }
    }
    if (spawned) {
      sparkPos.needsUpdate = true;
      sparkCol.needsUpdate = true;
      sparkVel.needsUpdate = true;
      sparkBirth.needsUpdate = true;
      sparkLife.needsUpdate = true;
      sparkSeed.needsUpdate = true;
    }
    sparks.m.uniforms.uTime!.value = t;
    const fog = scene.fog as THREE.Fog | null;
    backend.commit(t, fog);

    // Live flock centres for labels and the idle gaze.
    for (let k = 0; k < C; k++) {
      center[k * 3] = 0;
      center[k * 3 + 1] = 0;
      center[k * 3 + 2] = 0;
    }
    for (let i = 0; i < n; i++) {
      const k = slot[i]!;
      center[k * 3] = center[k * 3]! + pos[i * 3]!;
      center[k * 3 + 1] = center[k * 3 + 1]! + pos[i * 3 + 1]!;
      center[k * 3 + 2] = center[k * 3 + 2]! + pos[i * 3 + 2]!;
    }
    for (let k = 0; k < C; k++) {
      const c = Math.max(1, count[k]!);
      center[k * 3] = center[k * 3]! / c;
      center[k * 3 + 1] = center[k * 3 + 1]! / c;
      center[k * 3 + 2] = center[k * 3 + 2]! / c;
    }

    // 3) Call ring: a ring of light that leaves the viewer and spreads ahead.
    if (callRing.current) {
      if (callAge < 2.4) {
        callRing.current.visible = true;
        camera.getWorldDirection(vD);
        callRing.current.position
          .set(flight.callX, flight.callY, flight.callZ)
          .addScaledVector(vD, 3 + callAge * 9);
        const sc = 1.5 + callAge * 7;
        callRing.current.scale.set(sc, sc, 1);
        (callRing.current.material as THREE.SpriteMaterial).opacity = (1 - callAge / 2.4) * 0.7;
      } else {
        callRing.current.visible = false;
      }
    }

    // 4) Name labels on the closest birds.
    const lg = labelGroup.current;
    if (lg) {
      order.sort((a, b) => near[a]! - near[b]!);
      for (let q = 0; q < LABELS; q++) {
        const s = lg.children[q] as THREE.Sprite | undefined;
        if (!s) continue;
        const i = order[q]!;
        const d = near[i]!;
        if (d > 10 || i === selectedIndex) {
          s.visible = false;
          continue;
        }
        const m = s.material as THREE.SpriteMaterial;
        const tex = labelFor(i);
        if (m.map !== tex) {
          m.map = tex;
          m.needsUpdate = true;
        }
        s.visible = true;
        s.position.set(pos[i * 3]!, pos[i * 3 + 1]! + 0.8, pos[i * 3 + 2]!);
        m.opacity = (1 - smoothstep(5.5, 10, d)) * 0.85;
      }
    }

    // 5) Halo, trail and landing ring on the selected bird.
    if (selectedIndex >= 0) {
      const sx = pos[selectedIndex * 3]!;
      const sy = pos[selectedIndex * 3 + 1]!;
      const sz = pos[selectedIndex * 3 + 2]!;
      if (halo.current) {
        halo.current.visible = true;
        halo.current.position.set(sx, sy, sz);
        const pulse = 2.4 + Math.sin(t * 2.6) * 0.3;
        halo.current.scale.set(pulse, pulse, 1);
      }
      const pAttr = trail.g.getAttribute("position") as THREE.BufferAttribute;
      const cAttr = trail.g.getAttribute("color") as THREE.BufferAttribute;
      const pa = pAttr.array as Float32Array;
      if (lastTrailIdx.current !== selectedIndex) {
        for (let k = 0; k < TRAIL; k++) {
          pa[k * 3] = sx;
          pa[k * 3 + 1] = sy;
          pa[k * 3 + 2] = sz;
        }
        lastTrailIdx.current = selectedIndex;
      } else {
        pa.copyWithin(3, 0, (TRAIL - 1) * 3);
        pa[0] = sx;
        pa[1] = sy;
        pa[2] = sz;
      }
      const ca = cAttr.array as Float32Array;
      const fc = fog ? fog.color : tmpC.set("#c5dbec");
      for (let k = 0; k < TRAIL; k++) {
        const f = k / (TRAIL - 1);
        ca[k * 3] = WARM.r + (fc.r - WARM.r) * f;
        ca[k * 3 + 1] = WARM.g + (fc.g - WARM.g) * f;
        ca[k * 3 + 2] = WARM.b + (fc.b - WARM.b) * f;
      }
      pAttr.needsUpdate = true;
      cAttr.needsUpdate = true;
      trail.line.visible = true;

      if (ring.current) {
        const age = (performance.now() - flight.landedAt) / 1000;
        if (flight.landedAt > 0 && age < 1.6) {
          ring.current.visible = true;
          ring.current.position.set(sx, sy, sz);
          const s = 1.5 + age * 4.5;
          ring.current.scale.set(s, s, 1);
          (ring.current.material as THREE.SpriteMaterial).opacity = (1 - age / 1.6) * 0.7;
        } else {
          ring.current.visible = false;
        }
      }
    } else {
      if (halo.current) halo.current.visible = false;
      if (ring.current) ring.current.visible = false;
      trail.line.visible = false;
      lastTrailIdx.current = -1;
    }
  });

  // Tap / click: pick the nearest bird on screen, otherwise beat the wings once.
  useEffect(() => {
    const el = gl.domElement;
    let downX = 0;
    let downY = 0;
    let downAt = 0;
    let pointerType = "mouse";
    const onDown = (e: PointerEvent) => {
      downX = e.clientX;
      downY = e.clientY;
      downAt = performance.now();
      pointerType = e.pointerType;
    };
    const v = new THREE.Vector3();
    const onUp = (e: PointerEvent) => {
      const moved = Math.hypot(e.clientX - downX, e.clientY - downY);
      if (moved > 7 || performance.now() - downAt > 450) return;
      const rect = el.getBoundingClientRect();
      const px = e.clientX - rect.left;
      const py = e.clientY - rect.top;
      const radius = pointerType === "touch" ? 36 : 26;
      let best = -1;
      let bestD = radius;
      const pos = flock.pos;
      for (let i = 0; i < n; i++) {
        v.set(pos[i * 3]!, pos[i * 3 + 1]!, pos[i * 3 + 2]!).project(camera);
        if (v.z > 1 || v.z < -1) continue;
        const sx = ((v.x + 1) / 2) * size.width;
        const sy = ((1 - v.y) / 2) * size.height;
        const d = Math.hypot(sx - px, sy - py);
        if (d < bestD) {
          best = i;
          bestD = d;
        }
      }
      if (best >= 0) {
        onPick(space.points[best]!);
      } else {
        flight.flapRequested = true;
        playFlap();
      }
    };
    el.addEventListener("pointerdown", onDown);
    el.addEventListener("pointerup", onUp);
    return () => {
      el.removeEventListener("pointerdown", onDown);
      el.removeEventListener("pointerup", onUp);
    };
  }, [camera, gl, size, n, flock, onPick, space.points, flight]);

  return (
    <>
      {backend.objects.map((o, i) => (
        <primitive key={i} object={o} />
      ))}
      <primitive object={sparks.points} />
      <primitive object={trail.line} />
      <sprite ref={halo} visible={false} frustumCulled={false}>
        <spriteMaterial
          map={haloTex}
          transparent
          depthWrite={false}
          depthTest={false}
          blending={THREE.AdditiveBlending}
          toneMapped={false}
          opacity={0.5}
        />
      </sprite>
      <sprite ref={ring} visible={false} frustumCulled={false}>
        <spriteMaterial
          map={ringTex}
          transparent
          depthWrite={false}
          depthTest={false}
          toneMapped={false}
        />
      </sprite>
      <sprite ref={callRing} visible={false} frustumCulled={false}>
        <spriteMaterial
          map={ringTex}
          transparent
          depthWrite={false}
          depthTest={false}
          blending={THREE.AdditiveBlending}
          toneMapped={false}
        />
      </sprite>
      <group ref={labelGroup}>
        {Array.from({ length: LABELS }, (_, q) => (
          <sprite key={q} visible={false} scale={[2.6, 0.65, 1]} frustumCulled={false}>
            <spriteMaterial
              transparent
              depthWrite={false}
              depthTest={false}
              toneMapped={false}
              opacity={0}
            />
          </sprite>
        ))}
      </group>
      <FlockLabels space={space} lang={lang} centers={flock.center} clusterIds={flock.clusterIds} />
    </>
  );
}

/** Flock names riding above each flock's live centre; fade with distance. */
function FlockLabels({
  space,
  lang,
  centers,
  clusterIds,
}: {
  space: EmbeddingSpace;
  lang: "ko" | "en";
  centers: Float32Array;
  clusterIds: number[];
}) {
  const { camera } = useThree();
  const group = useRef<THREE.Group>(null);
  const items = useMemo(
    () =>
      clusterIds.map((id, k) => {
        const c = space.clusters.find((x) => x.id === id);
        const name = c ? (lang === "ko" ? c.name_ko : c.name_en) || c.name_ko : `#${id}`;
        const count = c?.count ?? 0;
        return {
          k,
          id,
          tex: makeLabelTexture(name, `${count} ${lang === "ko" ? "명" : "artists"}`),
        };
      }),
    [space.clusters, lang, clusterIds],
  );
  useEffect(() => () => items.forEach((i) => i.tex.dispose()), [items]);

  useFrame(() => {
    const g = group.current;
    if (!g) return;
    for (let i = 0; i < g.children.length; i++) {
      const s = g.children[i] as THREE.Sprite;
      const k = items[i]!.k;
      s.position.set(centers[k * 3]!, centers[k * 3 + 1]! + 3.8, centers[k * 3 + 2]!);
      const d = s.position.distanceTo(camera.position);
      const o = smoothstep(9, 18, d) * (1 - smoothstep(50, 100, d)) * 0.8;
      (s.material as THREE.SpriteMaterial).opacity = clamp(o, 0, 1);
    }
  });

  return (
    <group ref={group}>
      {items.map(({ id, tex }) => (
        <sprite key={id} scale={[6.4, 1.6, 1]} frustumCulled={false}>
          <spriteMaterial map={tex} transparent depthWrite={false} toneMapped={false} opacity={0} />
        </sprite>
      ))}
    </group>
  );
}
