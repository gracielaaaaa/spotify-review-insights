import { q, send, safeId } from "./_db.js";
// Single classified review by original review_id.
export default (req, res) => send(res, async () => {
  const id = req.query.id;
  if (!safeId(id)) throw new Error("bad review id");
  const r = await q(`reviews?review_id=eq.${encodeURIComponent(id)}&select=*`);
  return r[0] || null;
});
