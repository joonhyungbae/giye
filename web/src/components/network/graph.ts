// SPDX-License-Identifier: AGPL-3.0-only
/**
 * Network mode — graph model and measures.
 *
 * Nodes are artists; two artists are tied when they took part in the same event. Each event adds
 * 1/(n−1) to every tie among its n participants (Newman 2001), so an 83-person camp binds each
 * participant with the same total weight as a two-person show. Communities are found with Louvain
 * (weighted modularity); brokerage is betweenness centrality over the unweighted ties (Brandes).
 */
import type { NetEvent, NetworkData } from "@/lib/giye.network";

export type NetFilter = {
  roster: boolean;
  cv: boolean;
  /** inclusive year range; events without a year always count */
  y0: number;
  y1: number;
  /** a tie is drawn only when the two artists shared at least this many events */
  minShared: number;
};

export type Edge = { a: number; b: number; w: number; events: number[] };

export type Graph = {
  n: number;
  edges: Edge[];
  /** per node: indices into edges */
  adj: number[][];
  degree: Int32Array;
  strength: Float64Array;
  /** per node: neighbour node indices (for brokerage, computed on demand) */
  neighbours: number[][];
  /** per node: events counted under the filter */
  eventsOf: number[][];
  /** events that passed the filter */
  events: number[];
  community: Int32Array;
  communities: Array<{ id: number; members: number[]; events: Array<[number, number]> }>;
  modularity: number;
  betweenness: Float64Array;
  components: number;
  /** nodes with at least one tie */
  connected: number;
  density: number;
};

export function eventMembers(e: NetEvent, f: Pick<NetFilter, "roster" | "cv">): number[] {
  if (f.roster && f.cv) return [...new Set([...e.roster, ...e.cv])];
  return f.roster ? e.roster : f.cv ? e.cv : [];
}

export function yearSpan(data: NetworkData): [number, number] {
  const ys = data.events.map((e) => e.year).filter((y): y is number => y != null);
  return ys.length ? [Math.min(...ys), Math.max(...ys)] : [2000, new Date().getFullYear()];
}

export function buildGraph(data: NetworkData, f: NetFilter): Graph {
  const n = data.artists.length;
  const pair = new Map<number, Edge>();
  const eventsOf: number[][] = Array.from({ length: n }, () => []);
  const used: number[] = [];
  data.events.forEach((e, ei) => {
    if (e.year != null && (e.year < f.y0 || e.year > f.y1)) return;
    const m = eventMembers(e, f);
    if (m.length < 2) return;
    used.push(ei);
    const w = 1 / (m.length - 1);
    for (const a of m) eventsOf[a]!.push(ei);
    for (let i = 0; i < m.length; i++)
      for (let j = i + 1; j < m.length; j++) {
        const a = Math.min(m[i]!, m[j]!);
        const b = Math.max(m[i]!, m[j]!);
        const key = a * n + b;
        let ed = pair.get(key);
        if (!ed) {
          ed = { a, b, w: 0, events: [] };
          pair.set(key, ed);
        }
        ed.w += w;
        ed.events.push(ei);
      }
  });
  const edges = [...pair.values()].filter((e) => e.events.length >= f.minShared);
  const adj: number[][] = Array.from({ length: n }, () => []);
  const degree = new Int32Array(n);
  const strength = new Float64Array(n);
  edges.forEach((e, k) => {
    adj[e.a]!.push(k);
    adj[e.b]!.push(k);
    degree[e.a]! += 1;
    degree[e.b]! += 1;
    strength[e.a]! += e.w;
    strength[e.b]! += e.w;
  });

  const { community, q } = louvain(n, edges);
  const byCom = new Map<number, number[]>();
  for (let i = 0; i < n; i++) {
    if (degree[i] === 0) continue;
    const c = community[i]!;
    if (!byCom.has(c)) byCom.set(c, []);
    byCom.get(c)!.push(i);
  }
  // number communities by size, largest first, so labels stay readable
  const ordered = [...byCom.entries()].sort((p, q2) => q2[1].length - p[1].length);
  const renumber = new Map(ordered.map(([c], k) => [c, k] as const));
  for (let i = 0; i < n; i++) community[i] = degree[i] === 0 ? -1 : renumber.get(community[i]!)!;
  const communities = ordered.map(([, members], id) => {
    // the events that most of this community shares: what holds it together
    const count = new Map<number, number>();
    for (const i of members) for (const ei of eventsOf[i]!) count.set(ei, (count.get(ei) ?? 0) + 1);
    const events = [...count.entries()]
      .filter(([, c]) => c >= 2)
      .sort((p, q2) => q2[1] - p[1])
      .slice(0, 3);
    return { id, members, events };
  });

  const neighbours = adj.map((list, i) =>
    list.map((k) => (edges[k]!.a === i ? edges[k]!.b : edges[k]!.a)),
  );
  const connected = degree.reduce((s, d) => s + (d > 0 ? 1 : 0), 0);
  return {
    n,
    edges,
    adj,
    degree,
    strength,
    eventsOf,
    events: used,
    community,
    communities,
    modularity: q,
    neighbours,
    // Brandes is O(nodes × ties) — over a second here — so brokerage is not part of drawing the
    // graph; the panel asks for it once the picture is up (see NetworkStudy).
    betweenness: EMPTY_BETWEENNESS,
    components: countComponents(neighbours, degree),
    connected,
    density: connected > 1 ? (2 * edges.length) / (connected * (connected - 1)) : 0,
  };
}

/* ------------------------------------------------------------------ */
/* Louvain                                                              */
/* ------------------------------------------------------------------ */

/** Weighted Louvain, deterministic (nodes visited in index order). Isolated nodes keep their own id. */
export function louvain(
  n: number,
  edges: Array<{ a: number; b: number; w: number }>,
): { community: Int32Array; q: number } {
  // adjacency with self loops holding twice the internal weight, so k[i] = Σ adj[i]
  let adj: Map<number, number>[] = Array.from({ length: n }, () => new Map());
  for (const e of edges) {
    adj[e.a]!.set(e.b, (adj[e.a]!.get(e.b) ?? 0) + e.w);
    adj[e.b]!.set(e.a, (adj[e.b]!.get(e.a) ?? 0) + e.w);
  }
  const member = new Int32Array(n).map((_, i) => i); // original node → current super-node
  const m2 = edges.reduce((s, e) => s + 2 * e.w, 0);
  if (m2 === 0) return { community: member, q: 0 };

  for (let level = 0; level < 12; level++) {
    const N = adj.length;
    const k = adj.map((row) => [...row.values()].reduce((s, v) => s + v, 0));
    const com = new Int32Array(N).map((_, i) => i);
    const tot = Float64Array.from(k);
    let movedAny = false;
    for (let pass = 0; pass < 32; pass++) {
      let moved = false;
      for (let i = 0; i < N; i++) {
        if (k[i] === 0) continue;
        const ci = com[i]!;
        const links = new Map<number, number>();
        for (const [j, w] of adj[i]!) {
          if (j === i) continue;
          links.set(com[j]!, (links.get(com[j]!) ?? 0) + w);
        }
        tot[ci]! -= k[i]!;
        let best = ci;
        let bestGain = (links.get(ci) ?? 0) - (tot[ci]! * k[i]!) / m2;
        for (const [c, win] of links) {
          const gain = win - (tot[c]! * k[i]!) / m2;
          if (gain > bestGain + 1e-12) {
            bestGain = gain;
            best = c;
          }
        }
        tot[best]! += k[i]!;
        if (best !== ci) {
          com[i] = best;
          moved = true;
          movedAny = true;
        }
      }
      if (!moved) break;
    }
    if (!movedAny) break;
    // aggregate communities into super-nodes
    const ids = new Map<number, number>();
    for (let i = 0; i < N; i++) if (!ids.has(com[i]!)) ids.set(com[i]!, ids.size);
    const next: Map<number, number>[] = Array.from({ length: ids.size }, () => new Map());
    for (let i = 0; i < N; i++) {
      const ci = ids.get(com[i]!)!;
      for (const [j, w] of adj[i]!) {
        const cj = ids.get(com[j]!)!;
        next[ci]!.set(cj, (next[ci]!.get(cj) ?? 0) + w);
      }
    }
    for (let v = 0; v < n; v++) member[v] = ids.get(com[member[v]!]!)!;
    adj = next;
  }

  // modularity on the original graph
  const inW = new Map<number, number>();
  const totW = new Map<number, number>();
  for (const e of edges) {
    const ca = member[e.a]!;
    const cb = member[e.b]!;
    totW.set(ca, (totW.get(ca) ?? 0) + e.w);
    totW.set(cb, (totW.get(cb) ?? 0) + e.w);
    if (ca === cb) inW.set(ca, (inW.get(ca) ?? 0) + 2 * e.w);
  }
  let q = 0;
  for (const [c, t] of totW) q += (inW.get(c) ?? 0) / m2 - (t / m2) ** 2;
  return { community: member, q };
}

/* ------------------------------------------------------------------ */
/* Betweenness and components                                           */
/* ------------------------------------------------------------------ */

const EMPTY_BETWEENNESS = new Float64Array(0);

/** Brokerage for a built graph; safe to call off the drawing path. */
export function graphBetweenness(g: Graph): Float64Array {
  return betweenness(g.neighbours);
}

/** Brandes betweenness on an unweighted undirected graph, scaled to [0, 1] by the pair count. */
export function betweenness(nb: number[][]): Float64Array {
  const n = nb.length;
  const cb = new Float64Array(n);
  const sigma = new Float64Array(n);
  const dist = new Int32Array(n);
  const delta = new Float64Array(n);
  const stack: number[] = [];
  const queue = new Int32Array(n);
  const preds: number[][] = Array.from({ length: n }, () => []);
  for (let s = 0; s < n; s++) {
    if (nb[s]!.length === 0) continue;
    stack.length = 0;
    for (let i = 0; i < n; i++) {
      preds[i]!.length = 0;
      sigma[i] = 0;
      dist[i] = -1;
      delta[i] = 0;
    }
    sigma[s] = 1;
    dist[s] = 0;
    let head = 0;
    let tail = 0;
    queue[tail++] = s;
    while (head < tail) {
      const v = queue[head++]!;
      stack.push(v);
      for (const w of nb[v]!) {
        if (dist[w]! < 0) {
          dist[w] = dist[v]! + 1;
          queue[tail++] = w;
        }
        if (dist[w] === dist[v]! + 1) {
          sigma[w]! += sigma[v]!;
          preds[w]!.push(v);
        }
      }
    }
    while (stack.length) {
      const w = stack.pop()!;
      for (const v of preds[w]!) delta[v]! += (sigma[v]! / sigma[w]!) * (1 + delta[w]!);
      if (w !== s) cb[w]! += delta[w]!;
    }
  }
  const connected = nb.filter((l) => l.length > 0).length;
  const norm = connected > 2 ? (connected - 1) * (connected - 2) : 1;
  for (let i = 0; i < n; i++) cb[i] = cb[i]! / norm; // undirected: each pair counted twice
  return cb;
}

function countComponents(nb: number[][], degree: Int32Array): number {
  const seen = new Uint8Array(nb.length);
  let count = 0;
  for (let s = 0; s < nb.length; s++) {
    if (seen[s] || degree[s] === 0) continue;
    count++;
    const stack = [s];
    seen[s] = 1;
    while (stack.length) {
      const v = stack.pop()!;
      for (const w of nb[v]!)
        if (!seen[w]) {
          seen[w] = 1;
          stack.push(w);
        }
    }
  }
  return count;
}
