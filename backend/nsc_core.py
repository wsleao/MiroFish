"""NSC Novix Simulation Core - backend entrypoint.

This file is the stable product entrypoint for Plan B.
It keeps the current simulation engine available while the codebase is being
migrated from compatibility wrappers to a clean SaaS architecture.
"""

from backend.run_executive import app, response_ok
from backend.run import BACKEND_VERSION, utc_now
from backend.nsc_store import persistence_status

PRODUCT = {
    "name": "NSC Novix Simulation Core",
    "short_name": "NSC",
    "project_type": "personal_project",
    "scope": "multi_segment_decision_simulation",
    "positioning": "SaaS platform for multi-source, multi-agent decision simulation.",
    "status": "plan_b_refactor_in_progress",
}

DATA_SOURCE_TYPES = [
    {"type": "files", "label": "Files", "examples": ["pdf", "txt", "csv", "json", "xlsx"]},
    {"type": "text", "label": "Text", "examples": ["briefing", "news", "transcript"]},
    {"type": "url", "label": "Links", "examples": ["web page", "document URL", "public source"]},
    {"type": "api", "label": "APIs", "examples": ["REST", "GraphQL", "ERP", "CRM"]},
    {"type": "database", "label": "Databases", "examples": ["PostgreSQL", "SQL Server", "MySQL"]},
    {"type": "webhook", "label": "Webhooks", "examples": ["event stream", "external trigger"]},
]


@app.get("/api/nsc/info")
async def nsc_info():
    return response_ok({
        "status": "ready",
        "product": PRODUCT,
        "backend_version": BACKEND_VERSION,
        "generated_at": utc_now(),
    })


@app.get("/api/nsc/data-source-types")
async def nsc_data_source_types():
    return response_ok({
        "status": "ready",
        "product": PRODUCT["name"],
        "data_source_types": DATA_SOURCE_TYPES,
        "generated_at": utc_now(),
    })


@app.get("/api/nsc/persistence-status")
async def nsc_persistence_status():
    status = await persistence_status()
    return response_ok({
        "status": "ready",
        "product": PRODUCT["name"],
        "persistence": status,
        "generated_at": utc_now(),
    })


@app.get("/api/nsc/refactor-status")
async def nsc_refactor_status():
    return response_ok({
        "status": "in_progress",
        "phase": "plan_b_backend_entrypoint",
        "completed": [
            "NSC product identity",
            "SaaS login screen on frontend",
            "multi-source input UI",
            "executive report wrapper",
            "GraphRAG prompt ontology wrapper",
            "clean NSC backend entrypoint",
            "PostgreSQL schema draft",
            "PostgreSQL store helper",
        ],
        "next": [
            "wire simulations into PostgreSQL persistence",
            "unified simulation engine without stacked middleware",
            "structured agent output schema",
            "connector ingestion jobs",
            "server-side SaaS permission model",
        ],
        "generated_at": utc_now(),
    })
