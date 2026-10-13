// Backend data access: server-side reads from the Supabase Postgres REST API.
// Credentials come from Vercel environment variables, never from browser code.
const BASE = process.env.SUPABASE_URL;
const KEY = process.env.SUPABASE_PUBLISHABLE_KEY;

export async function q(path) {
  if (!BASE || !KEY) throw new Error("SUPABASE_URL / SUPABASE_PUBLISHABLE_KEY not configured");
  const r = await fetch(`${BASE}/rest/v1/${path}`, { headers: { apikey: KEY, Authorization: `Bearer ${KEY}` } });
  if (!r.ok) throw new Error(`db ${r.status}: ${(await r.text()).slice(0, 200)}`);
  return r.json();
}

export function send(res, fn) {
  return fn()
    .then((data) => {
      res.setHeader("Cache-Control", "s-maxage=300, stale-while-revalidate=600");
      res.status(200).json(data);
    })
    .catch((e) => res.status(500).json({ error: String(e.message || e) }));
}

export const safeId = (s) => /^[A-Za-z0-9_.\-]{1,80}$/.test(s || "");
