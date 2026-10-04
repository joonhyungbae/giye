// SPDX-License-Identifier: AGPL-3.0-only
/**
 * Scrape guard — runs before every server request.
 *
 * robots.txt sends Content-Signal ai-train=no. A crawler whose vendor says it
 * collects pages for model training, or that publishes an open crawl corpus,
 * is refused here and named in public/robots.txt. Search crawlers and
 * user-triggered fetchers that answer with a citation stay allowed and are
 * rate limited. Scripted clients are refused too.
 *
 * This raises the cost of harvesting rather than making it impossible: user
 * agents can be spoofed and the counters live in one server instance's memory.
 * Put a CDN/WAF rate rule in front for stronger protection.
 */

const WINDOW_MS = 60_000;
const MAX_TRACKED = 20_000;

/**
 * A missing or blank variable keeps the built-in limit. A non-integer or a
 * non-positive value would turn the guard off or block every visitor, so that
 * also keeps the built-in limit.
 */
function positiveInt(name: string, fallback: number): number {
  const raw = process.env[name]?.trim();
  if (!raw) return fallback;
  const n = Number(raw);
  if (!Number.isInteger(n) || n < 1) return fallback;
  return n;
}

/** requests per minute per IP (pages + server functions). GIYE_GUARD_HUMAN_PER_MIN, default 120. */
const HUMAN_PER_MIN = positiveInt("GIYE_GUARD_HUMAN_PER_MIN", 120);
/** GIYE_GUARD_CRAWLER_PER_MIN, default 60. */
const CRAWLER_PER_MIN = positiveInt("GIYE_GUARD_CRAWLER_PER_MIN", 60);
/** distinct pages/records one IP may open within this window before it is treated as a harvester.
 *  GIYE_GUARD_ENUM_WINDOW_S, default 600 (10 minutes). */
const ENUM_WINDOW_MS = positiveInt("GIYE_GUARD_ENUM_WINDOW_S", 10 * 60) * 1000;
/** GIYE_GUARD_ENUM_LIMIT, default 80. */
const ENUM_LIMIT = positiveInt("GIYE_GUARD_ENUM_LIMIT", 80);
/** GIYE_GUARD_BLOCK_S, default 900 (15 minutes). */
const BLOCK_MS = positiveInt("GIYE_GUARD_BLOCK_S", 15 * 60) * 1000;

/**
 * Training and open-corpus crawlers. Refused, and listed the same way in
 * public/robots.txt. Matched on the product token so a longer allowed token
 * is not caught: "gptbot" is not "ChatGPT-User", and "claudebot" is not
 * "Claude-SearchBot" or "Claude-User".
 *
 * - GPTBot — crawls pages that may train OpenAI foundation models.
 *   https://developers.openai.com/api/docs/bots
 * - Google-Extended — robots.txt token for using crawled pages to train Gemini
 *   and for grounding. Google says it has no separate HTTP user agent; Search
 *   crawling is Googlebot, which stays allowed.
 *   https://developers.google.com/crawling/docs/crawlers-fetchers/google-common-crawlers
 * - Applebot-Extended — robots.txt token for training Apple foundation models.
 *   Apple says it does not crawl; Search crawling is Applebot, which stays allowed.
 *   https://support.apple.com/en-us/119829
 * - ClaudeBot — collects pages that may train Anthropic models. Claude-User
 *   (a person asked Claude to fetch) and Claude-SearchBot (search) stay allowed.
 *   https://support.claude.com/en/articles/8896518-does-anthropic-crawl-data-from-the-web-and-how-can-site-owners-block-the-crawler
 * - CCBot — Common Crawl's open web corpus.
 *   https://commoncrawl.org/ccbot
 * - Bytespider — Toutiao documents this as its search crawler and publishes no
 *   separate token that opts the same crawl out of model training, so it is
 *   refused with the training crawlers.
 *   https://www.toutiao.com/article/6759451726496940557/
 */
const TRAINING_UA =
  /(?:^|[^a-z0-9])(?:gptbot|google-extended|applebot-extended|claudebot|ccbot|bytespider)(?:[^a-z0-9]|$)/i;

/**
 * Search engines, and user-triggered fetchers that answer with a citation.
 * Allowed, rate limited, and exempt from the enumeration check.
 *
 * OAI-SearchBot and ChatGPT-User: search results, and a fetch a ChatGPT user
 * triggered (https://developers.openai.com/api/docs/bots). Claude-User and
 * Claude-SearchBot: the same split at Anthropic (URL under TRAINING_UA).
 * PerplexityBot indexes pages for search and is not the training crawler
 * (https://docs.perplexity.ai/docs/resources/perplexity-crawlers). Googlebot,
 * Bingbot, Yeti (Naver) and Daum are search crawlers.
 */
const CRAWLER_UA =
  /googlebot|google-inspectiontool|bingbot|yeti\/|daum|duckduckbot|applebot|slurp|oai-searchbot|chatgpt-user|claude-user|claude-searchbot|perplexitybot|perplexity-user|mistralai-user|facebookexternalhit|twitterbot|slackbot|kakaotalk-scrap|discordbot|linkedinbot/i;

/** HTTP libraries, headless browsers and bulk or SEO harvesters. The harvester tokens are the same list as public/robots.txt, apart from the training tokens above. */
const BLOCKED_UA =
  /curl\/|wget|python-requests|python-urllib|python-httpx|aiohttp|httpx|scrapy|go-http-client|java\/|okhttp|node-fetch|axios|undici|got \(|libwww-perl|mechanize|httpclient|headlesschrome|phantomjs|puppeteer|playwright|selenium|diffbot|omgili|imagesiftbot|dataforseo|ahrefsbot|semrushbot|mj12bot|dotbot|blexbot|petalbot|crawler4j|colly|webcopier|httrack|sitesucker|offline explorer/i;

const PASS_PATH =
  /^\/(?:assets\/|models\/|samples\/|favicon\.|robots\.txt$|sitemap\.xml$|@vite\/|@id\/|@fs\/|node_modules\/|src\/|__)/;

type Bucket = {
  windowStart: number;
  count: number;
  seen: Map<string, number>;
  blockedUntil: number;
};

const buckets = new Map<string, Bucket>();

/**
 * The one header that carries the visitor's address, named by GIYE_TRUSTED_IP_HEADER
 * (production: "cf-connecting-ip"). Only the edge proxy may set it: the origin firewall
 * accepts Cloudflare's ranges only, so a visitor cannot forge it. Headers a client can
 * write itself (the first X-Forwarded-For entry) are never read, otherwise one client
 * could send a new value per request and never fill a bucket. Unset (local dev): no
 * per-IP limits, only the user-agent rule.
 */
const TRUSTED_IP_HEADER = process.env.GIYE_TRUSTED_IP_HEADER?.trim().toLowerCase() || null;

function clientIp(request: Request): string | null {
  if (!TRUSTED_IP_HEADER) return null;
  return request.headers.get(TRUSTED_IP_HEADER)?.trim() || null;
}

function refuse(status: 403 | 429, retryAfterSec?: number): Response {
  const body =
    status === 429
      ? "요청이 너무 많습니다. 잠시 후 다시 시도하세요. / Too many requests."
      : "자동화된 수집은 허용되지 않습니다. / Automated collection is not permitted.";
  return new Response(body, {
    status,
    headers: {
      "content-type": "text/plain; charset=utf-8",
      "cache-control": "no-store",
      ...(retryAfterSec ? { "retry-after": String(retryAfterSec) } : {}),
    },
  });
}

function prune(now: number) {
  if (buckets.size < MAX_TRACKED) return;
  for (const [k, b] of buckets) {
    if (now - b.windowStart > ENUM_WINDOW_MS && b.blockedUntil < now) buckets.delete(k);
  }
}

/** Returns a refusal Response, or null to let the request through. */
export function guardRequest(request: Request): Response | null {
  const url = new URL(request.url);
  if (PASS_PATH.test(url.pathname)) return null;

  const ua = request.headers.get("user-agent")?.trim() ?? "";
  if (!ua || TRAINING_UA.test(ua) || BLOCKED_UA.test(ua)) return refuse(403);
  const crawler = CRAWLER_UA.test(ua);

  const ip = clientIp(request);
  if (!ip) return null; // no trusted header (local dev): only the user-agent rule applies

  const now = Date.now();
  prune(now);
  let b = buckets.get(ip);
  if (!b) {
    b = { windowStart: now, count: 0, seen: new Map(), blockedUntil: 0 };
    buckets.set(ip, b);
  }
  if (b.blockedUntil > now) return refuse(429, Math.ceil((b.blockedUntil - now) / 1000));

  if (now - b.windowStart > WINDOW_MS) {
    b.windowStart = now;
    b.count = 0;
  }
  b.count += 1;
  if (b.count > (crawler ? CRAWLER_PER_MIN : HUMAN_PER_MIN)) {
    b.blockedUntil = now + (crawler ? WINDOW_MS : BLOCK_MS);
    return refuse(429, Math.ceil((b.blockedUntil - now) / 1000));
  }

  if (!crawler) {
    // distinct resources (a page path, or a server-function call with its payload)
    const key = url.pathname + (url.pathname.startsWith("/_serverFn/") ? url.search : "");
    b.seen.set(key, now);
    if (b.seen.size > ENUM_LIMIT) {
      for (const [k, t] of b.seen) if (now - t > ENUM_WINDOW_MS) b.seen.delete(k);
      if (b.seen.size > ENUM_LIMIT) {
        b.blockedUntil = now + BLOCK_MS;
        b.seen.clear();
        return refuse(429, BLOCK_MS / 1000);
      }
    }
  }
  return null;
}
