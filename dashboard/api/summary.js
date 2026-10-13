import { q, send } from "./_db.js";
// Overall metrics, run provenance, topic roll-up, AI memo + its claims.
export default (req, res) => send(res, async () => {
  const [metrics, runs, rollup, memo, claims] = await Promise.all([
    q("metrics?select=key,value"), q("runs?select=run_id,label_config,summary,published_at"),
    q("topic_rollup?select=*&order=severity_sum.desc"), q("memo?select=*"), q("claims?select=*&order=claim_id"),
  ]);
  return { metrics: Object.fromEntries(metrics.map((m) => [m.key, m.value])), run: runs[0] || null,
           topic_rollup: rollup, memo: memo[0] || null, claims, source: "supabase:postgres" };
});
