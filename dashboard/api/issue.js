import { q, send, safeId } from "./_db.js";
// One issue with its saved evidence and a page of member reviews (traceable review IDs).
export default (req, res) => send(res, async () => {
  const id = req.query.id;
  if (!safeId(id)) throw new Error("bad issue id");
  const limit = Math.min(parseInt(req.query.limit || "25", 10) || 25, 100);
  const [issue, members] = await Promise.all([
    q(`issues?issue_id=eq.${encodeURIComponent(id)}&select=*`),
    q(`reviews?issue_id=eq.${encodeURIComponent(id)}&cache_source_id=is.null&select=review_id,intent,severity,sentiment,evidence_quote,needs_review,review_month&order=severity.desc,review_id.asc&limit=${limit}`),
  ]);
  return { issue: issue[0] || null, members };
});
