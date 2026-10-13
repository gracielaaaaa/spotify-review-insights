-- Supabase / Postgres schema for the processed Spotify review analysis.
-- Loaded by `python -m pipeline publish` from saved pipeline outputs (no model calls).
-- RLS: public read-only (the data are public app-store reviews + derived aggregates).

create table if not exists runs (
  run_id text primary key,
  label_config text not null,
  summary jsonb not null,
  published_at timestamptz default now()
);

create table if not exists metrics (
  key text primary key,
  value jsonb not null
);

create table if not exists reviews (           -- completed classifications (slim)
  review_id text primary key,
  topic text not null, subtopic text, intent text not null,
  sentiment real not null, severity smallint not null,
  evidence_quote text not null, needs_review boolean not null,
  cache_source_id text, issue_id text,
  review_rating smallint, review_month text, in_declared_scope boolean
);
create index if not exists reviews_issue on reviews(issue_id);
create index if not exists reviews_topic on reviews(topic);

create table if not exists issues (            -- baseline ranking + model-suggested names
  issue_id text primary key, rank int not null, topic text not null, subtopic text,
  name text, description text,
  complaint_count int not null, severity_sum int not null, mean_severity numeric(10,6) not null,
  priority_score int not null, representative_ids jsonb, evidence jsonb
);

create table if not exists topic_rollup (
  topic text primary key, issues int, complaint_count int, severity_sum int, mean_severity numeric(10,6)
);

create table if not exists claims (
  claim_id text primary key, issue_id text not null, metric text not null, value text not null
);

create table if not exists memo (
  id int primary key default 1, run_id text, model text, recommendation text, memo_markdown text,
  checks jsonb, cited_claim_ids jsonb, cited_review_ids jsonb
);

create table if not exists monthly (           -- complaint share by month (supplementary trend)
  month text, topic text, complaints int, completed int, primary key (month, topic)
);

do $$
declare t text;
begin
  foreach t in array array['runs','metrics','reviews','issues','topic_rollup','claims','memo','monthly'] loop
    execute format('alter table %I enable row level security', t);
    execute format('drop policy if exists "public read" on %I', t);
    execute format('create policy "public read" on %I for select to anon, authenticated using (true)', t);
  end loop;
end $$;
