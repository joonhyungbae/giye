// SPDX-License-Identifier: AGPL-3.0-only
import { useFrame, useThree } from "@react-three/fiber";
import { useMemo, useRef } from "react";
import * as THREE from "three";
import { clamp, lerp, mulberry32 } from "./flightState";
import { makeCloudTexture, makeRadialTexture, makeRaysTexture } from "./textures";

export const SUN_DIR = new THREE.Vector3(0.5, 0.34, -0.8).normalize();

const LOW_HAZE = new THREE.Color("#ecdccb");
const HIGH_HAZE = new THREE.Color("#bcd3ea");

/**
 * Sky dome + sun + fog, all following the camera and responding to altitude:
 * low = hazier, warmer horizon; high = clearer, deeper zenith.
 */
export function Atmosphere() {
  const { camera, scene } = useThree();
  const dome = useRef<THREE.Mesh>(null);
  const sun = useRef<THREE.Sprite>(null);
  const rays = useRef<THREE.Sprite>(null);
  const raysTex = useMemo(() => makeRaysTexture(5), []);

  const skyMat = useMemo(
    () =>
      new THREE.ShaderMaterial({
        side: THREE.BackSide,
        depthWrite: false,
        fog: false,
        uniforms: {
          uTop: { value: new THREE.Color("#5A9AD3") },
          uDeep: { value: new THREE.Color("#3B76B5") },
          uMid: { value: new THREE.Color("#A2C6E6") },
          uBottom: { value: new THREE.Color("#F3E4D2") },
          uSunDir: { value: SUN_DIR.clone() },
          uAlt: { value: 0.35 },
          uFog: { value: new THREE.Color("#c5dbec") },
        },
        vertexShader: `
          varying vec3 vDir;
          void main() {
            vDir = position;
            gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0);
          }
        `,
        fragmentShader: `
          uniform vec3 uTop; uniform vec3 uDeep; uniform vec3 uMid; uniform vec3 uBottom;
          uniform vec3 uSunDir; uniform float uAlt; uniform vec3 uFog;
          varying vec3 vDir;
          void main() {
            vec3 d = normalize(vDir);
            float h = clamp(d.y * 0.5 + 0.5, 0.0, 1.0);
            vec3 top = mix(uTop, uDeep, uAlt * 0.55);
            float haze = mix(0.58, 0.40, uAlt);
            vec3 col = mix(uBottom, uMid, smoothstep(0.0, haze, h));
            col = mix(col, top, smoothstep(haze * 0.72, 1.0, h));
            // Horizon band takes the fog colour so distant objects dissolve seamlessly.
            float band = exp(-abs(d.y) * mix(5.0, 8.0, uAlt));
            col = mix(col, uFog, band * 0.92);
            float s = max(dot(d, uSunDir), 0.0);
            // Warm wash toward the sun, then the broad glow and the disc.
            col = mix(col, vec3(0.99, 0.86, 0.70), pow(s, 4.0) * 0.16 * (1.0 - h * 0.5));
            col += vec3(1.0, 0.90, 0.72) * pow(s, 16.0) * 0.16;
            col += vec3(1.0, 0.97, 0.90) * pow(s, 420.0) * 0.8;
            gl_FragColor = vec4(col, 1.0);
            #include <colorspace_fragment>
          }
        `,
      }),
    [],
  );

  const sunTex = useMemo(
    () =>
      makeRadialTexture([
        [0, "rgba(255,246,222,0.95)"],
        [0.18, "rgba(255,240,205,0.55)"],
        [0.5, "rgba(255,236,200,0.12)"],
        [1, "rgba(255,236,200,0)"],
      ]),
    [],
  );

  const fogColor = useMemo(() => new THREE.Color(), []);

  useFrame(() => {
    const y = camera.position.y;
    const f = clamp((y + 20) / 70, 0, 1);
    skyMat.uniforms.uAlt!.value = f;
    if (dome.current) dome.current.position.copy(camera.position);
    if (sun.current) sun.current.position.copy(camera.position).addScaledVector(SUN_DIR, 150);
    if (rays.current) {
      rays.current.position.copy(camera.position).addScaledVector(SUN_DIR, 152);
      const m = rays.current.material as THREE.SpriteMaterial;
      m.rotation += 0.0006;
      m.opacity = 0.42 + Math.sin(performance.now() * 0.0004) * 0.08;
    }
    const fog = scene.fog as THREE.Fog | null;
    if (fog) {
      fog.near = lerp(26, 68, f);
      fog.far = lerp(108, 178, f);
      fog.color.copy(fogColor.lerpColors(LOW_HAZE, HIGH_HAZE, f));
      (skyMat.uniforms.uFog!.value as THREE.Color).copy(fog.color);
    }
  });

  return (
    <>
      <fog attach="fog" args={["#c5dbec", 40, 140]} />
      <hemisphereLight args={["#c4dbf1", "#e6d2bd", 0.95]} />
      <directionalLight
        position={[SUN_DIR.x * 100, SUN_DIR.y * 100, SUN_DIR.z * 100]}
        intensity={1.35}
        color="#ffe6c4"
      />
      <mesh ref={dome} frustumCulled={false}>
        <sphereGeometry args={[190, 40, 20]} />
        <primitive object={skyMat} attach="material" />
      </mesh>
      <sprite ref={rays} scale={[150, 150, 1]} frustumCulled={false}>
        <spriteMaterial
          map={raysTex}
          transparent
          depthWrite={false}
          depthTest={false}
          blending={THREE.AdditiveBlending}
          fog={false}
          toneMapped={false}
          opacity={0.45}
        />
      </sprite>
      <sprite ref={sun} scale={[38, 38, 1]} frustumCulled={false}>
        <spriteMaterial
          map={sunTex}
          transparent
          depthWrite={false}
          depthTest={false}
          blending={THREE.AdditiveBlending}
          fog={false}
          toneMapped={false}
          opacity={0.55}
        />
      </sprite>
    </>
  );
}

/** Puffy cumulus, some below the flock (altitude cue), some above. Slow wind drift. */
export function Clouds() {
  const tex = useMemo(() => makeCloudTexture(11), []);
  const clouds = useMemo(() => {
    const rnd = mulberry32(2026);
    const out: Array<{
      x: number;
      y: number;
      z: number;
      drift: number;
      parts: Array<{ dx: number; dy: number; dz: number; s: number; o: number }>;
    }> = [];
    // A sea of cloud below the flocks, and a few high wisps above.
    for (let i = 0; i < 34; i++) {
      const a = rnd() * Math.PI * 2;
      const sea = i < 26;
      const r = sea ? 22 + rnd() * 95 : 30 + rnd() * 70;
      const y = sea ? -16 - rnd() * 10 : 18 + rnd() * 22;
      const puffs = sea ? 4 + Math.floor(rnd() * 3) : 2 + Math.floor(rnd() * 2);
      const parts = [];
      for (let k = 0; k < puffs; k++) {
        parts.push({
          dx: (rnd() - 0.5) * (sea ? 26 : 12),
          dy: (rnd() - 0.5) * (sea ? 4 : 2.5),
          dz: (rnd() - 0.5) * (sea ? 18 : 8),
          s: sea ? 16 + rnd() * 18 : 8 + rnd() * 8,
          o: sea ? 0.3 + rnd() * 0.25 : 0.22 + rnd() * 0.2,
        });
      }
      out.push({ x: Math.cos(a) * r, y, z: Math.sin(a) * r, parts, drift: 0.25 + rnd() * 0.5 });
    }
    return out;
  }, []);
  const group = useRef<THREE.Group>(null);

  useFrame((_, dt) => {
    const g = group.current;
    if (!g) return;
    for (let i = 0; i < g.children.length; i++) {
      const c = g.children[i]!;
      c.position.x += clouds[i]!.drift * dt * 0.5;
      if (c.position.x > 150) c.position.x = -150;
    }
  });

  return (
    <group ref={group}>
      {clouds.map((c, i) => (
        <group key={i} position={[c.x, c.y, c.z]}>
          {c.parts.map((p, k) => (
            <sprite key={k} position={[p.dx, p.dy, p.dz]} scale={[p.s * 1.7, p.s, 1]}>
              <spriteMaterial
                map={tex}
                color="#f6f0e8"
                transparent
                opacity={p.o}
                depthWrite={false}
                toneMapped={false}
              />
            </sprite>
          ))}
        </group>
      ))}
    </group>
  );
}

/** Tiny motes streaming past the camera — the main speed cue. */
export function Motes({ count = 240 }: { count?: number }) {
  const { camera } = useThree();
  const ref = useRef<THREE.Points>(null);
  const positions = useMemo(() => {
    const rnd = mulberry32(99);
    const arr = new Float32Array(count * 3);
    for (let i = 0; i < count * 3; i++) arr[i] = (rnd() - 0.5) * 32;
    return arr;
  }, [count]);
  const moteTex = useMemo(
    () =>
      makeRadialTexture(
        [
          [0, "rgba(255,255,255,1)"],
          [0.45, "rgba(255,255,255,0.55)"],
          [1, "rgba(255,255,255,0)"],
        ],
        32,
      ),
    [],
  );

  useFrame(() => {
    const pts = ref.current;
    if (!pts) return;
    const attr = pts.geometry.getAttribute("position") as THREE.BufferAttribute;
    const arr = attr.array as Float32Array;
    const c = camera.position;
    for (let i = 0; i < count; i++) {
      for (let ax = 0; ax < 3; ax++) {
        const idx = i * 3 + ax;
        const cam = ax === 0 ? c.x : ax === 1 ? c.y : c.z;
        const d = arr[idx]! - cam;
        if (d > 16) arr[idx] = arr[idx]! - 32;
        else if (d < -16) arr[idx] = arr[idx]! + 32;
      }
    }
    attr.needsUpdate = true;
  });

  return (
    <points ref={ref} frustumCulled={false}>
      <bufferGeometry>
        <bufferAttribute attach="attributes-position" args={[positions, 3]} />
      </bufferGeometry>
      <pointsMaterial
        map={moteTex}
        alphaTest={0.02}
        size={0.11}
        sizeAttenuation
        color="#ffffff"
        transparent
        opacity={0.55}
        depthWrite={false}
        toneMapped={false}
      />
    </points>
  );
}
