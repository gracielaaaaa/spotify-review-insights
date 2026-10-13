-- TEMPORARY: allow the loader (publishable key) to write. Apply, run `python -m pipeline publish`, then apply loader_close.sql.
do $$ declare t text; begin
  foreach t in array array['runs','metrics','reviews','issues','topic_rollup','claims','memo','monthly'] loop
    execute format('drop policy if exists "loader write" on %I', t);
    execute format('create policy "loader write" on %I for all to anon using (true) with check (true)', t);
    execute format('truncate table %I', t);
  end loop; end $$;
