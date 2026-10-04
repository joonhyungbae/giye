# Samples for the ledger ensemble (원장 합주)

Trimmed, level-matched MP3 excerpts used by `src/components/study/ensemble.ts`.

| folder | instrument | source | licence |
| --- | --- | --- | --- |
| vibes/ | Vibraphone, soft mallets | Versilian Community Sample Library (VCSL) | CC0 1.0 |
| marimba/ | Marimba (soft, medium) | VCSL | CC0 1.0 |
| glock/ | Glockenspiel (soft, medium) | VCSL | CC0 1.0 |
| piano/ | Salamander Grand Piano v3 (Yamaha C5), layers 5 and 10 | Alexander Holm | CC BY 3.0 |

- VCSL: https://github.com/sgossner/VCSL (Versilian Studios LLC, public domain).
- Salamander Grand Piano v3 by Alexander Holm, https://archive.org/details/SalamanderGrandPianoV3 — attribution required; the site credits it on the data page.

Files are named `<midi>_<layer>.mp3`. `manifest.json` lists every note; the sampler picks the nearest sampled pitch and shifts it.
The music itself borrows only the *process* of Terry Riley's "In C" (pulse, shared cells, free repetition, a bounded lead); no material from the score is included.
