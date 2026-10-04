// SPDX-License-Identifier: AGPL-3.0-only
import { useFrame, useThree } from "@react-three/fiber";
import { useEffect, useMemo } from "react";
import * as THREE from "three";
import { EffectComposer } from "three/examples/jsm/postprocessing/EffectComposer.js";
import { OutputPass } from "three/examples/jsm/postprocessing/OutputPass.js";
import { RenderPass } from "three/examples/jsm/postprocessing/RenderPass.js";
import { UnrealBloomPass } from "three/examples/jsm/postprocessing/UnrealBloomPass.js";

/**
 * Soft bloom for the sun, the halo and sunlit wings. Skipped on small screens
 * where the extra passes cost more than they give.
 */
export function Bloom({ minWidth = 900 }: { minWidth?: number }) {
  const { gl, scene, camera, size } = useThree();
  const enabled = size.width >= minWidth;

  const composer = useMemo(() => {
    if (!enabled) return null;
    const c = new EffectComposer(gl);
    c.addPass(new RenderPass(scene, camera));
    const bloom = new UnrealBloomPass(
      new THREE.Vector2(Math.max(1, size.width / 2), Math.max(1, size.height / 2)),
      0.26, // strength
      0.55, // radius
      0.94, // threshold — only the sun, halos and the brightest wings
    );
    c.addPass(bloom);
    c.addPass(new OutputPass());
    return c;
    // size handled below
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [gl, scene, camera, enabled]);

  useEffect(() => {
    if (!composer) return;
    composer.setSize(size.width, size.height);
    composer.setPixelRatio(gl.getPixelRatio());
  }, [composer, size, gl]);

  useEffect(() => () => composer?.dispose(), [composer]);

  // Priority > 0 takes over rendering from R3F.
  useFrame(
    () => {
      if (composer) composer.render();
    },
    enabled ? 1 : 0,
  );

  return null;
}
