// SPDX-License-Identifier: AGPL-3.0-only
/**
 * Client-side query vectorization mirroring scripts/feature_schema.py
 * + scripts/build_embedding_space.py (FNV-1a text hash).
 */

export type EmbeddingVocab = {
  medium: string[];
  technique: string[];
  theme: string[];
  region: string[];
  frame: string[];
  activity_type: string[];
  decade: string[];
  text_hash_dim: number;
};

export type EmbeddingPoint = {
  id: string;
  name_ko: string | null;
  name_en: string | null;
  x: number;
  y: number;
  z: number;
  cluster: number;
  medium_tags: string[];
  regions: string[];
  frame_codes: string[];
  /** Omitted in the light flight payload to keep first paint fast. */
  vector?: number[];
};

export type EmbeddingCluster = {
  id: number;
  name_ko: string;
  name_en: string;
  count: number;
  centroid: [number, number, number];
  member_ids: string[];
  top_medium?: string | null;
  top_frame?: string | null;
};

export type EmbeddingSpace = {
  generated_at: string;
  method: string;
  vocab?: EmbeddingVocab;
  block_weights?: Record<string, number>;
  dims: number;
  n_artists: number;
  n_clusters: number;
  clusters: EmbeddingCluster[];
  points: EmbeddingPoint[];
};

const BLOCK = {
  medium: 1.4,
  technique: 1.0,
  theme: 1.0,
  region: 0.8,
  frame: 1.2,
  activity_type: 1.0,
  decade: 0.9,
  text: 1.1,
} as const;

function tokenize(s: string): string[] {
  const lower = (s || "").toLowerCase();
  const parts = lower.match(/[가-힣]{2,}|[a-z0-9]{2,}/g) ?? [];
  const grams: string[] = [];
  for (const p of parts) {
    grams.push(p);
    if (p.length >= 3 && /[가-힣]/.test(p)) {
      for (let i = 0; i < p.length - 1; i++) grams.push(p.slice(i, i + 2));
    }
  }
  return grams;
}

function fnv1a(tok: string, dim: number): number {
  let h = 2166136261;
  const bytes = new TextEncoder().encode(tok);
  for (const b of bytes) {
    h ^= b;
    h = Math.imul(h, 16777619) >>> 0;
  }
  return h % dim;
}

function oneHot(keys: string[], present: Set<string>, weight: number): number[] {
  return keys.map((k) => (present.has(k) ? weight : 0));
}

function frameFamily(code: string): string {
  return (code || "").replace(/-\d{4}$/, "") || "OTHER";
}

/** Build a query vector comparable to artist.vector in embedding.json */
export function queryToVector(q: string, vocab: EmbeddingVocab): number[] {
  const qRaw = q || "";
  const qLower = qRaw.toLowerCase();
  const hit = (keys: string[]) => new Set(keys.filter((k) => qLower.includes(k.toLowerCase()) || qRaw.includes(k)));

  const medium = hit(vocab.medium);
  const technique = hit(vocab.technique);
  const theme = hit(vocab.theme);
  const region = hit(vocab.region);
  const frame = hit(vocab.frame);

  const typeVec = vocab.activity_type.map((t) =>
    qLower.includes(t.replace(/_/g, " ")) || qLower.includes(t) ? BLOCK.activity_type : 0,
  );
  const decadeVec = vocab.decade.map((d) => (qLower.includes(d.toLowerCase()) ? BLOCK.decade : 0));

  const dim = vocab.text_hash_dim || 64;
  const textVec = new Array(dim).fill(0);
  for (const t of tokenize(qRaw)) {
    textVec[fnv1a(t, dim)] += 1;
  }
  const textWeighted = textVec.map((v) => BLOCK.text * Math.log1p(v));

  const parts = [
    ...oneHot(vocab.medium, medium, BLOCK.medium),
    ...oneHot(vocab.technique, technique, BLOCK.technique),
    ...oneHot(vocab.theme, theme, BLOCK.theme),
    ...oneHot(vocab.region, region, BLOCK.region),
    ...oneHot(vocab.frame, frame, BLOCK.frame),
    ...typeVec,
    ...decadeVec,
    ...textWeighted,
  ];

  // Frame codes in the query are matched through vocab.frame above.
  const norm = Math.hypot(...parts) || 1;
  return parts.map((v) => v / norm);
}

export function cosine(a: number[], b: number[]): number {
  const n = Math.min(a.length, b.length);
  let dot = 0;
  let na = 0;
  let nb = 0;
  for (let i = 0; i < n; i++) {
    dot += a[i]! * b[i]!;
    na += a[i]! * a[i]!;
    nb += b[i]! * b[i]!;
  }
  const d = Math.sqrt(na) * Math.sqrt(nb);
  return d ? dot / d : 0;
}

export function searchPoints(
  space: EmbeddingSpace,
  query: string,
  limit = 8,
): Array<EmbeddingPoint & { score: number }> {
  const q = query.trim();
  if (!q) return [];

  const ql = q.toLowerCase();
  const toks = tokenize(q);

  const scored = space.points.map((p) => {
    const hay = [
      p.name_ko ?? "",
      p.name_en ?? "",
      ...p.medium_tags,
      ...p.regions,
      ...p.frame_codes,
      frameFamily(p.frame_codes[0] ?? ""),
    ]
      .join(" ")
      .toLowerCase();

    let score = 0;
    if (hay.includes(ql)) score += 2.5;
    for (const tok of toks) {
      if (hay.includes(tok)) score += 0.55;
    }
    // Optional semantic score when full vectors are present
    if (p.vector?.length && space.vocab) {
      const qv = queryToVector(q, space.vocab);
      score += cosine(qv, p.vector) * 1.2;
    }
    return { ...p, score };
  });

  return scored.filter((p) => p.score > 0).sort((a, b) => b.score - a.score).slice(0, limit);
}

/** Colors that read as birds against a bright sky (ink / earth / sea). */
export const CLUSTER_COLORS = [
  "#1a1a1a",
  "#2c4a3e",
  "#5c3317",
  "#1e3a5f",
  "#4a3728",
  "#3d2b4a",
  "#2f4f4f",
  "#4a2020",
];
