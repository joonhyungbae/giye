// SPDX-License-Identifier: AGPL-3.0-only
import { useFrame, useThree } from "@react-three/fiber";
import { useEffect, useMemo, useRef, type MutableRefObject } from "react";
import * as THREE from "three";
import {
  BOOST,
  CRUISE,
  MAX_SPEED,
  MIN_SPEED,
  clamp,
  damp,
  dampAngle,
  shortAngle,
  smoothstep,
  type FlightState,
} from "./flightState";

type FlightPath = {
  curve: THREE.CatmullRomCurve3;
  start: number;
  duration: number;
  /** Where to sit relative to the bird once alongside. */
  offset: THREE.Vector3;
};

const UP = new THREE.Vector3(0, 1, 0);

/**
 * Approach path toward a (moving) bird: continue on the current heading,
 * swing wide, then settle alongside it. The final stretch is blended toward
 * the bird's live position so the landing tracks the flock.
 */
function buildPath(from: THREE.Vector3, fwd: THREE.Vector3, target: THREE.Vector3): FlightPath {
  const toT = target.clone().sub(from);
  const dist = Math.max(toT.length(), 0.01);
  const dir = toT.clone().normalize();
  const side = new THREE.Vector3().crossVectors(UP, dir).normalize();
  if (side.lengthSq() < 1e-4) side.set(1, 0, 0);
  if (fwd.dot(side) < 0) side.multiplyScalar(-1);

  const p0 = from.clone();
  const p1 = from.clone().addScaledVector(fwd, clamp(dist * 0.22, 3, 10));
  const p2 = target
    .clone()
    .addScaledVector(side, clamp(5 + dist * 0.18, 6, 13))
    .addScaledVector(UP, 3.2 + Math.min(4, dist * 0.05))
    .addScaledVector(dir, -Math.min(6, dist * 0.15));
  const perchDir = dir.clone().multiplyScalar(-1).addScaledVector(side, 0.6).normalize();
  const offset = perchDir.multiplyScalar(6.5).addScaledVector(UP, 1.5);
  const p3 = target.clone().add(offset);

  const curve = new THREE.CatmullRomCurve3([p0, p1, p2, p3], false, "centripetal", 0.5);
  const len = curve.getLength();
  const duration = clamp(len / 8.5, 3.2, 7) * 1000;
  return { curve, start: performance.now(), duration, offset };
}

const easeInOut = (t: number) => (t < 0.5 ? 2 * t * t : 1 - Math.pow(-2 * t + 2, 2) / 2);

export function FlightRig({
  keys,
  flight,
}: {
  keys: MutableRefObject<Record<string, boolean>>;
  flight: FlightState;
}) {
  const { camera, gl } = useThree();
  const s = useRef({
    yaw: 0,
    pitch: -0.12,
    roll: 0,
    tYaw: 0,
    tPitch: -0.12,
    lastYaw: 0,
    speed: 0,
    bob: 0,
    idle: 0,
    lift: 0,
    kick: 0,
    path: null as FlightPath | null,
    offset: new THREE.Vector3(),
    initialised: false,
  }).current;
  const reduced = useMemo(
    () =>
      typeof window !== "undefined" &&
      !!window.matchMedia?.("(prefers-reduced-motion: reduce)").matches,
    [],
  );
  const fwd = useMemo(() => new THREE.Vector3(), []);
  const euler = useMemo(() => new THREE.Euler(0, 0, 0, "YXZ"), []);
  const bird = useMemo(() => new THREE.Vector3(), []);
  const desired = useMemo(() => new THREE.Vector3(), []);
  const tmp = useMemo(() => new THREE.Vector3(), []);
  const prevPos = useMemo(() => new THREE.Vector3(), []);

  const leavePerch = useMemo(
    () => () => {
      if (!flight.perched) return;
      flight.perched = false;
      flight.followIndex = -1;
      s.speed = Math.max(s.speed, CRUISE * 0.6);
    },
    [flight, s],
  );

  // Pointer steering (mouse or touch drag) and wheel throttle.
  useEffect(() => {
    const el = gl.domElement;
    let dragging = false;
    let lastX = 0;
    let lastY = 0;
    let type = "mouse";
    const onDown = (e: PointerEvent) => {
      dragging = true;
      lastX = e.clientX;
      lastY = e.clientY;
      type = e.pointerType;
      el.setPointerCapture?.(e.pointerId);
    };
    const onMove = (e: PointerEvent) => {
      if (!dragging) return;
      const dx = e.clientX - lastX;
      const dy = e.clientY - lastY;
      lastX = e.clientX;
      lastY = e.clientY;
      if (Math.abs(dx) + Math.abs(dy) < 1) return;
      const k = type === "touch" ? 0.0048 : 0.0027;
      if (s.path) return;
      s.tYaw -= dx * k;
      s.tPitch = clamp(s.tPitch - dy * k, -1.05, 0.95);
      flight.interacted = true;
      flight.lastInputAt = performance.now();
      leavePerch();
    };
    const onUp = (e: PointerEvent) => {
      dragging = false;
      try {
        el.releasePointerCapture?.(e.pointerId);
      } catch {
        /* not captured */
      }
    };
    const onWheel = (e: WheelEvent) => {
      e.preventDefault();
      if (s.path) return;
      flight.interacted = true;
      flight.lastInputAt = performance.now();
      leavePerch();
      s.speed = clamp(s.speed - e.deltaY * 0.012, MIN_SPEED, BOOST);
    };
    el.addEventListener("pointerdown", onDown);
    el.addEventListener("pointermove", onMove);
    window.addEventListener("pointerup", onUp);
    window.addEventListener("pointercancel", onUp);
    el.addEventListener("wheel", onWheel, { passive: false });
    return () => {
      el.removeEventListener("pointerdown", onDown);
      el.removeEventListener("pointermove", onMove);
      window.removeEventListener("pointerup", onUp);
      window.removeEventListener("pointercancel", onUp);
      el.removeEventListener("wheel", onWheel);
    };
  }, [gl, s, flight, leavePerch]);

  const readBird = (index: number, out: THREE.Vector3) => {
    const live = flight.live;
    if (!live || index < 0 || index * 3 + 2 >= live.length) return false;
    out.set(live[index * 3]!, live[index * 3 + 1]!, live[index * 3 + 2]!);
    return true;
  };

  useFrame((_, rawDt) => {
    const dt = Math.min(rawDt, 0.05);
    const cam = camera as THREE.PerspectiveCamera;
    const k = keys.current;
    const now = performance.now();

    if (!s.initialised) {
      cam.position.set(0, 11, 46);
      s.initialised = true;
    }
    prevPos.copy(cam.position);

    // Consume a fly request from the HUD / picker.
    if (flight.pendingIndex >= 0) {
      const idx = flight.pendingIndex;
      flight.pendingIndex = -1;
      if (readBird(idx, bird)) {
        cam.getWorldDirection(fwd);
        s.path = buildPath(cam.position, fwd, bird);
        s.offset.copy(s.path.offset);
        flight.followIndex = idx;
        flight.flying = true;
        flight.perched = false;
      }
    }
    // One wing-beat: a push forward and a little lift, then glide on.
    if (flight.flapRequested) {
      flight.flapRequested = false;
      flight.interacted = true;
      flight.lastInputAt = now;
      if (!s.path) {
        leavePerch();
        s.speed = Math.min(s.speed + 3.4, MAX_SPEED);
        s.lift = 1;
        s.kick = 1;
      }
    }
    if (flight.resumeRequested) {
      flight.resumeRequested = false;
      flight.interacted = true;
      flight.perched = true; // make leavePerch run its transition
      leavePerch();
    }

    if (s.path) {
      const p = s.path;
      const t = clamp((now - p.start) / p.duration, 0, 1);
      const e = easeInOut(t);
      const pos = p.curve.getPointAt(e);
      const tan = p.curve.getTangentAt(e);
      if (readBird(flight.followIndex, bird)) {
        // Blend the tail of the path onto the moving bird.
        desired.copy(bird).add(p.offset);
        pos.lerp(desired, smoothstep(0.5, 1, t));
        tmp.copy(bird).sub(pos).normalize();
        tan.lerp(tmp, smoothstep(0.55, 1, t)).normalize();
      }
      s.tYaw = Math.atan2(-tan.x, -tan.z);
      s.tPitch = Math.asin(clamp(tan.y, -1, 1));
      cam.position.copy(pos);
      if (t >= 1) {
        s.path = null;
        flight.flying = false;
        flight.perched = true;
        flight.landedAt = now;
      }
    } else if (flight.perched && readBird(flight.followIndex, bird)) {
      // Flying alongside: hold the offset, keep eyes on the bird.
      desired.copy(bird).add(s.offset);
      cam.position.lerp(desired, 1 - Math.exp(-3.5 * dt));
      tmp.copy(bird).sub(cam.position).normalize();
      s.tYaw = Math.atan2(-tmp.x, -tmp.z);
      s.tPitch = Math.asin(clamp(tmp.y, -1, 1));
    } else {
      if (flight.perched) flight.perched = false; // lost the bird
      const turn = 1.45 * dt;
      if (k["a"] || k["arrowleft"]) s.tYaw += turn;
      if (k["d"] || k["arrowright"]) s.tYaw -= turn;
      if (k["e"] || k["arrowup"]) s.tPitch += 0.9 * dt;
      if (k["q"] || k["arrowdown"]) s.tPitch -= 0.9 * dt;
      s.tPitch = clamp(s.tPitch, -1.05, 0.95);
      const boost = !!k["shift"];
      if (k["w"]) s.speed += 9 * dt;
      else if (k["s"]) s.speed -= 10 * dt;
      // Hands off: glide on, then come to rest and simply watch the flocks.
      else s.speed = damp(s.speed, boost ? BOOST * 0.85 : 0, boost ? 0.55 : 0.45, dt);
      if (s.speed > 0.3) s.speed += -Math.sin(s.pitch) * 4.2 * dt; // dive gains, climb bleeds
      s.speed = clamp(s.speed, 0, boost ? BOOST : MAX_SPEED);
      if (s.speed < 0.04 && !k["w"]) s.speed = 0;

      const r = Math.hypot(cam.position.x, cam.position.z);
      // Soft bounds: nudge back toward the flocks when drifting away.
      if (r > 78) {
        const want = Math.atan2(cam.position.x, cam.position.z);
        const w = clamp((r - 78) / 30, 0, 1) * dt * 1.6;
        s.tYaw += shortAngle(want - s.tYaw) * w;
      }
      if (cam.position.y > 58) s.tPitch -= (cam.position.y - 58) * 0.03 * dt;
      if (cam.position.y < -18) s.tPitch += (-18 - cam.position.y) * 0.03 * dt;

      // Idle gaze: after a long stillness with no flock in view, turn toward one.
      if (s.speed < 0.15 && now - flight.lastInputAt > 15000 && flight.flockCenters) {
        const fc = flight.flockCenters;
        let bestDot = -2;
        let bestYaw = s.tYaw;
        const cy = Math.cos(s.yaw);
        const sy = Math.sin(s.yaw);
        for (let q = 0; q + 2 < fc.length; q += 3) {
          const vx = fc[q]! - cam.position.x;
          const vz = fc[q + 2]! - cam.position.z;
          const len = Math.hypot(vx, vz) || 1;
          const dot = (-sy * vx - cy * vz) / len;
          if (dot > bestDot) {
            bestDot = dot;
            bestYaw = Math.atan2(-vx, -vz);
          }
        }
        if (bestDot < 0.55) s.tYaw = dampAngle(s.tYaw, bestYaw, 0.12, dt);
      }

      const cp = Math.cos(s.pitch);
      fwd.set(-Math.sin(s.yaw) * cp, Math.sin(s.pitch), -Math.cos(s.yaw) * cp);
      cam.position.addScaledVector(fwd, s.speed * dt);
      cam.position.y += s.lift * 2.4 * dt;
      s.lift = damp(s.lift, 0, 2.2, dt);
    }

    // Keyboard input while alongside a bird hands control back.
    const anyMove =
      k["w"] ||
      k["s"] ||
      k["a"] ||
      k["d"] ||
      k["e"] ||
      k["q"] ||
      k["arrowup"] ||
      k["arrowdown"] ||
      k["arrowleft"] ||
      k["arrowright"] ||
      k["shift"];
    if (anyMove) {
      flight.interacted = true;
      flight.lastInputAt = now;
      if (!s.path) leavePerch();
    }

    // Orientation with inertia; bank into turns.
    s.yaw = dampAngle(s.yaw, s.tYaw, 4.8, dt);
    s.pitch = damp(s.pitch, s.tPitch, 4.8, dt);
    const yawRate = shortAngle(s.yaw - s.lastYaw) / Math.max(dt, 1e-3);
    s.lastYaw = s.yaw;
    const targetRoll = clamp(yawRate * 0.55, -0.7, 0.7);
    s.roll = damp(s.roll, targetRoll, 3.2, dt);

    // Wing-beat nod: quicker when flapping (accelerating), slow when gliding.
    const flapping = !s.path && !flight.perched && !!(k["w"] || k["shift"]);
    s.bob += dt * (flapping || s.kick > 0.2 ? 7.5 : 2.0);
    s.kick = damp(s.kick, 0, 3, dt);
    const moving = clamp(s.speed / CRUISE, 0, 1);
    const bobAmp =
      (reduced ? 0 : flapping ? 0.022 : flight.perched ? 0.006 : 0.011 * moving) + s.kick * 0.03;
    // At rest the view breathes very slowly, like hanging on a thermal.
    s.idle += dt;
    const restSway = reduced ? 0 : (1 - moving) * (s.path ? 0 : 1) * 0.012;
    euler.set(
      s.pitch + Math.sin(s.bob) * bobAmp + Math.sin(s.idle * 0.31) * restSway,
      s.yaw + Math.sin(s.idle * 0.19) * restSway * 1.4,
      s.roll + Math.sin(s.bob * 0.5) * bobAmp * 0.6 + Math.sin(s.idle * 0.23) * restSway * 0.5,
      "YXZ",
    );
    cam.quaternion.setFromEuler(euler);

    // Actual speed this frame (also meaningful on paths and alongside a bird).
    if (s.path || flight.perched) {
      s.speed = prevPos.distanceTo(cam.position) / Math.max(rawDt, 1e-3);
    }

    // Speed widens the view a little.
    const fovTarget = 60 + (reduced ? 0 : clamp((s.speed - CRUISE) / (BOOST - CRUISE), 0, 1) * 13);
    if (Math.abs(cam.fov - fovTarget) > 0.02) {
      cam.fov = damp(cam.fov, fovTarget, 4, dt);
      cam.updateProjectionMatrix();
    }

    flight.speed = s.speed;
    flight.speed01 = clamp(s.speed / BOOST, 0, 1);
    flight.altitude = cam.position.y;
  });

  return null;
}
