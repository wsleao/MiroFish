-- NSC Novix Simulation Core - SaaS persistence schema
-- Target: PostgreSQL / Supabase compatible

create schema if not exists nsc_admin;
create schema if not exists nsc_raw;
create schema if not exists nsc_core;
create schema if not exists nsc_ai;
create schema if not exists nsc_audit;

create table if not exists nsc_admin.tenants (
  id uuid primary key default gen_random_uuid(),
  name text not null,
  slug text unique not null,
  plan text not null default 'personal',
  status text not null default 'active',
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create table if not exists nsc_admin.users (
  id uuid primary key default gen_random_uuid(),
  tenant_id uuid not null references nsc_admin.tenants(id),
  name text not null,
  email text not null,
  credential_hash text,
  status text not null default 'active',
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique(tenant_id, email)
);

create table if not exists nsc_admin.user_roles (
  id uuid primary key default gen_random_uuid(),
  tenant_id uuid not null references nsc_admin.tenants(id),
  user_id uuid not null references nsc_admin.users(id),
  role text not null,
  created_at timestamptz not null default now(),
  unique(user_id, role)
);

create table if not exists nsc_core.workspaces (
  id uuid primary key default gen_random_uuid(),
  tenant_id uuid not null references nsc_admin.tenants(id),
  name text not null,
  description text,
  created_by uuid references nsc_admin.users(id),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create table if not exists nsc_core.data_sources (
  id uuid primary key default gen_random_uuid(),
  tenant_id uuid not null references nsc_admin.tenants(id),
  workspace_id uuid references nsc_core.workspaces(id),
  source_type text not null,
  name text not null,
  connection_config jsonb not null default '{}'::jsonb,
  metadata jsonb not null default '{}'::jsonb,
  status text not null default 'draft',
  created_by uuid references nsc_admin.users(id),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create table if not exists nsc_raw.ingestion_batches (
  id uuid primary key default gen_random_uuid(),
  tenant_id uuid not null references nsc_admin.tenants(id),
  workspace_id uuid references nsc_core.workspaces(id),
  data_source_id uuid references nsc_core.data_sources(id),
  source_type text not null,
  status text not null default 'received',
  record_count int not null default 0,
  started_at timestamptz not null default now(),
  completed_at timestamptz,
  metadata jsonb not null default '{}'::jsonb
);

create table if not exists nsc_raw.source_records (
  id uuid primary key default gen_random_uuid(),
  tenant_id uuid not null references nsc_admin.tenants(id),
  batch_id uuid references nsc_raw.ingestion_batches(id),
  data_source_id uuid references nsc_core.data_sources(id),
  external_id text,
  raw_payload jsonb not null,
  content_text text,
  content_hash text,
  imported_at timestamptz not null default now()
);

create table if not exists nsc_core.simulations (
  id uuid primary key default gen_random_uuid(),
  tenant_id uuid not null references nsc_admin.tenants(id),
  workspace_id uuid references nsc_core.workspaces(id),
  title text not null,
  objective text not null,
  segment text,
  status text not null default 'created',
  configuration jsonb not null default '{}'::jsonb,
  ontology jsonb not null default '{}'::jsonb,
  graph_summary jsonb not null default '{}'::jsonb,
  created_by uuid references nsc_admin.users(id),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create table if not exists nsc_ai.agents (
  id uuid primary key default gen_random_uuid(),
  tenant_id uuid not null references nsc_admin.tenants(id),
  simulation_id uuid not null references nsc_core.simulations(id),
  name text not null,
  profile text not null,
  role_description text,
  configuration jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now()
);

create table if not exists nsc_ai.agent_actions (
  id uuid primary key default gen_random_uuid(),
  tenant_id uuid not null references nsc_admin.tenants(id),
  simulation_id uuid not null references nsc_core.simulations(id),
  agent_id uuid references nsc_ai.agents(id),
  round_number int not null,
  action_type text not null default 'analysis',
  content text not null,
  structured_output jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now()
);

create table if not exists nsc_ai.reports (
  id uuid primary key default gen_random_uuid(),
  tenant_id uuid not null references nsc_admin.tenants(id),
  simulation_id uuid not null references nsc_core.simulations(id),
  title text not null,
  status text not null default 'draft',
  sections jsonb not null default '[]'::jsonb,
  recommendation jsonb not null default '{}'::jsonb,
  generated_at timestamptz not null default now()
);

create table if not exists nsc_audit.events (
  id uuid primary key default gen_random_uuid(),
  tenant_id uuid references nsc_admin.tenants(id),
  user_id uuid references nsc_admin.users(id),
  event_type text not null,
  entity_type text,
  entity_id text,
  payload jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now()
);

create index if not exists idx_nsc_source_records_tenant_batch on nsc_raw.source_records(tenant_id, batch_id);
create index if not exists idx_nsc_simulations_tenant_status on nsc_core.simulations(tenant_id, status);
create index if not exists idx_nsc_agent_actions_simulation on nsc_ai.agent_actions(simulation_id, round_number);
create index if not exists idx_nsc_reports_simulation on nsc_ai.reports(simulation_id);
