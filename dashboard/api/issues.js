import { q, send } from "./_db.js";
// Baseline ranking: priority_score = severity_sum, ordered by score desc then issue_id asc.
export default (req, res) => send(res, () =>
  q("issues?select=issue_id,rank,topic,subtopic,name,description,complaint_count,severity_sum,mean_severity,priority_score,representative_ids&order=rank.asc"));
