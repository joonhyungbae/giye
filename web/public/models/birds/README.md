# Bird models

Free morph-target flight loops from the three.js examples repository
(`examples/models/gltf/{Flamingo,Parrot,Stork}.glb`), credited there to
Mirada (ro.me). Used by `src/components/flight/birdModels.ts` as a prototype;
replace with purchased assets before public launch (see project notes).

- All three face +Z; wing-beat is a morph-target animation on `weights`.
- Baked vertex colours are converted to luminance at load; the flock palette
  supplies hue via `instanceColor`.
