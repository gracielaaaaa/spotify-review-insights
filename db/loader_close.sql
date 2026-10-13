-- Remove loader write access; tables stay public read-only.
do $$ declare t text; begin
  foreach t in array array['runs','metrics','reviews','issues','topic_rollup','claims','memo','monthly'] loop
    execute format('drop policy if exists "loader write" on %I', t);
  end loop; end $$;
