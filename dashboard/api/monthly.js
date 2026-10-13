import { q, send } from "./_db.js";
// Complaint counts per topic per month with the month's completed-record denominator.
export default (req, res) => send(res, () => q("monthly?select=*&order=month.asc"));
