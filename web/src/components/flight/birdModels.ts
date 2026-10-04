// SPDX-License-Identifier: AGPL-3.0-only
import * as THREE from "three";
import { GLTFLoader } from "three/examples/jsm/loaders/GLTFLoader.js";

/**
 * A morph-target bird model prepared for instancing:
 * one geometry, one animation clip, and a dummy mesh the mixer drives so we can
 * copy its morphTargetInfluences into any instance with InstancedMesh.setMorphAt.
 */
export type BirdSpecies = {
  name: string;
  geometry: THREE.BufferGeometry;
  root: THREE.Object3D;
  dummy: THREE.Mesh;
  clip: THREE.AnimationClip;
  duration: number;
  /** Uniform scale that brings the wingspan to ~2 scene units × the species factor. */
  scale: number;
  /** Clip time at which the wings are most level — the glide pose. */
  glideTime: number;
};

export type SpeciesSpec = { name: string; url: string; size: number };

/** three.js example birds (morph-target flight loops). All face +Z. */
export const FREE_BIRDS: SpeciesSpec[] = [
  { name: "stork", url: "/models/birds/Stork.glb", size: 1.15 },
  { name: "flamingo", url: "/models/birds/Flamingo.glb", size: 1.0 },
  { name: "parrot", url: "/models/birds/Parrot.glb", size: 0.8 },
];

/**
 * Replace baked vertex colours with their normalised luminance so the flock
 * palette (instanceColor) decides hue while the model keeps its shading detail.
 */
function toLuminance(geometry: THREE.BufferGeometry) {
  const col = geometry.getAttribute("color") as THREE.BufferAttribute | undefined;
  if (!col) return;
  const n = col.count;
  const lum = new Float32Array(n);
  let max = 1e-4;
  for (let i = 0; i < n; i++) {
    const l = 0.299 * col.getX(i) + 0.587 * col.getY(i) + 0.114 * col.getZ(i);
    lum[i] = l;
    if (l > max) max = l;
  }
  const out = new THREE.BufferAttribute(new Float32Array(n * 3), 3);
  for (let i = 0; i < n; i++) {
    const v = 0.45 + 0.55 * (lum[i]! / max);
    out.setXYZ(i, v, v, v);
  }
  geometry.setAttribute("color", out);
}

/**
 * Sample the flight clip and return the time where the wingtips sit closest to
 * the body plane (wings level = glide). Uses the morph deltas of the tip vertices.
 */
function findGlideTime(
  root: THREE.Object3D,
  mesh: THREE.Mesh,
  clip: THREE.AnimationClip,
  minX: number,
  maxX: number,
): number {
  const g = mesh.geometry as THREE.BufferGeometry;
  const pos = g.getAttribute("position") as THREE.BufferAttribute;
  const targets = g.morphAttributes.position ?? [];
  const tips: number[] = [];
  for (let i = 0; i < pos.count; i++) {
    const x = pos.getX(i);
    if (x > maxX - 0.02 * (maxX - minX) || x < minX + 0.02 * (maxX - minX)) tips.push(i);
  }
  if (tips.length === 0 || targets.length === 0) return 0;
  const mixer = new THREE.AnimationMixer(root);
  mixer.clipAction(clip).play();
  let best = 0;
  let bestScore = Infinity;
  const samples = 32;
  for (let k = 0; k < samples; k++) {
    const t = (k / samples) * clip.duration;
    mixer.setTime(t);
    const w = mesh.morphTargetInfluences ?? [];
    let score = 0;
    for (const i of tips) {
      let y = pos.getY(i);
      for (let j = 0; j < targets.length; j++) {
        const wj = w[j] ?? 0;
        if (wj !== 0) y += targets[j]!.getY(i) * wj;
      }
      score += Math.abs(y);
    }
    if (score < bestScore) {
      bestScore = score;
      best = t;
    }
  }
  mixer.stopAllAction();
  return best;
}

export async function loadBirdSpecies(specs: SpeciesSpec[] = FREE_BIRDS): Promise<BirdSpecies[]> {
  const loader = new GLTFLoader();
  const out: BirdSpecies[] = [];
  for (const spec of specs) {
    const glb = await loader.loadAsync(spec.url);
    let mesh: THREE.Mesh | null = null;
    glb.scene.traverse((o) => {
      if (!mesh && (o as THREE.Mesh).isMesh) mesh = o as THREE.Mesh;
    });
    if (!mesh || !glb.animations[0]) continue;
    const m = mesh as THREE.Mesh;
    const geometry = m.geometry as THREE.BufferGeometry;
    toLuminance(geometry);
    if (!geometry.getAttribute("normal")) geometry.computeVertexNormals();
    // Wingspan from the base pose only: computeBoundingBox() would include the
    // morph targets, which for these models are far larger than the rest pose.
    const pos = geometry.getAttribute("position") as THREE.BufferAttribute;
    let minX = Infinity;
    let maxX = -Infinity;
    for (let i = 0; i < pos.count; i++) {
      const x = pos.getX(i);
      if (x < minX) minX = x;
      if (x > maxX) maxX = x;
    }
    const span = Math.max(maxX - minX, 1e-3);
    // Empirically the flight poses open ~2x wider than the rest pose, so aim low.
    const scale = (1.25 / span) * spec.size;
    const clip = glb.animations[0];
    out.push({
      glideTime: findGlideTime(glb.scene, m, clip, minX, maxX),
      name: spec.name,
      geometry,
      root: glb.scene,
      dummy: m,
      clip,
      duration: clip.duration,
      scale,
    });
  }
  return out;
}
