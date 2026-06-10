// NSC Novix Simulation Core - Neo4j graph schema
// Execute this file in Neo4j Browser, Neo4j Aura Query, or cypher-shell.
// This is NOT PostgreSQL SQL. The PostgreSQL/Supabase schema is in backend/sql/001_nsc_saas_schema.sql

CREATE CONSTRAINT nsc_tenant_id IF NOT EXISTS
FOR (n:Tenant)
REQUIRE n.id IS UNIQUE;

CREATE CONSTRAINT nsc_workspace_id IF NOT EXISTS
FOR (n:Workspace)
REQUIRE n.id IS UNIQUE;

CREATE CONSTRAINT nsc_simulation_id IF NOT EXISTS
FOR (n:Simulation)
REQUIRE n.id IS UNIQUE;

CREATE CONSTRAINT nsc_datasource_id IF NOT EXISTS
FOR (n:DataSource)
REQUIRE n.id IS UNIQUE;

CREATE CONSTRAINT nsc_entity_id IF NOT EXISTS
FOR (n:Entity)
REQUIRE n.id IS UNIQUE;

CREATE CONSTRAINT nsc_agent_id IF NOT EXISTS
FOR (n:Agent)
REQUIRE n.id IS UNIQUE;

CREATE CONSTRAINT nsc_decision_id IF NOT EXISTS
FOR (n:Decision)
REQUIRE n.id IS UNIQUE;

CREATE CONSTRAINT nsc_report_id IF NOT EXISTS
FOR (n:Report)
REQUIRE n.id IS UNIQUE;

CREATE INDEX nsc_entity_type IF NOT EXISTS
FOR (n:Entity)
ON (n.type);

CREATE INDEX nsc_entity_name IF NOT EXISTS
FOR (n:Entity)
ON (n.name);

CREATE INDEX nsc_simulation_status IF NOT EXISTS
FOR (n:Simulation)
ON (n.status);

// Example seed for validation
MERGE (tenant:Tenant {id: 'tenant_personal_default'})
SET tenant.name = 'NSC Workspace',
    tenant.project_type = 'personal_project',
    tenant.updated_at = datetime();

MERGE (workspace:Workspace {id: 'workspace_default'})
SET workspace.name = 'Default Workspace',
    workspace.updated_at = datetime();

MERGE (tenant)-[:OWNS_WORKSPACE]->(workspace);

RETURN 'NSC Neo4j graph schema ready' AS status;
