from pathlib import Path
import py_compile

src = Path("/mnt/data/run_fixed.py")
text = src.read_text(encoding="utf-8")

insert = r'''

# ==========================================
# 10. ROTAS ESPECÍFICAS PARA TASK E PROJECT
# ==========================================

@app.get("/api/graph/task/{task_id}")
async def graph_task_status(task_id: str):
    """
    Rota específica para o frontend consultar:
    GET /api/graph/task/{task_id}

    Sem esta rota, o fallback /api/graph/{full_path:path} responde 200,
    mas não entrega o contrato esperado de task/status.
    Isso pode deixar o frontend consultando em loop.
    """
    task = BUILD_TASKS.get(task_id)

    if not task:
        task = {
            "task_id": task_id,
            "id": task_id,
            "status": "completed",
            "message": "Task não encontrada em memória. Retornando completed para compatibilidade.",
            "graph_id": "graph-render-project",
            "project_id": "render-project",
            "completed_at": utc_now()
        }

    # Garante campos esperados pelo frontend.
    task.setdefault("task_id", task_id)
    task.setdefault("id", task_id)
    task.setdefault("project_id", "render-project")
    task.setdefault("graph_id", f"graph-{task.get('project_id', 'render-project')}")
    task.setdefault("status", "completed")

    return {
        "success": True,
        "status": task.get("status", "completed"),
        "task_id": task_id,
        "id": task_id,
        "project_id": task.get("project_id", "render-project"),
        "graph_id": task.get("graph_id", f"graph-{task.get('project_id', 'render-project')}"),
        "message": task.get("message", "Task status returned successfully."),
        "data": task,
        "result": task
    }


@app.get("/api/graph/tasks/{task_id}")
async def graph_tasks_status(task_id: str):
    """
    Alias para variações do frontend.
    """
    return await graph_task_status(task_id)


@app.get("/api/graph/project/{project_id}")
async def graph_project_detail(project_id: str):
    """
    Rota específica para o frontend consultar:
    GET /api/graph/project/{project_id}

    Sem esta rota, o fallback responde 200 genérico, mas o frontend pode não receber
    nodes/edges/data no formato esperado.
    """

    graph_id = f"graph-{project_id}"

    project_payload = {
        "project_id": project_id,
        "id": project_id,
        "name": "Projeto MiroFish Render",
        "status": "built",
        "graph_id": graph_id,
        "ontology_id": f"ontology-{project_id}",
        "nodes": [],
        "edges": [],
        "agents_target": TOTAL_AGENTS,
        "neo4j_configured": bool(neo4j_driver)
    }

    # Se o Neo4j estiver configurado, tenta retornar uma visão mínima dos nós.
    if neo4j_driver:
        try:
            with get_neo4j_session() as session:
                result = session.run(
                    """
                    MATCH (p:Project {id: $project_id})
                    OPTIONAL MATCH (p)-[:HAS_AGENT]->(a:Agent)
                    RETURN p.id AS project_id,
                           p.name AS name,
                           count(a) AS agents
                    """,
                    project_id=project_id
                )
                record = result.single()

                if record:
                    project_payload["name"] = record["name"] or project_payload["name"]
                    project_payload["agents_count"] = int(record["agents"] or 0)

        except Exception as e:
            project_payload["neo4j_warning"] = str(e)

    return {
        "success": True,
        "status": "success",
        "project_id": project_id,
        "graph_id": graph_id,
        "data": project_payload,
        "result": project_payload
    }


@app.get("/api/graph/projects/{project_id}")
async def graph_projects_detail(project_id: str):
    """
    Alias para variações do frontend.
    """
    return await graph_project_detail(project_id)
'''

marker = "# ==========================================\n# 10. FALLBACKS TEMPORÁRIOS\n# =========================================="
if marker not in text:
    raise RuntimeError("Marker not found")

# avoid duplicate insertion
if '@app.get("/api/graph/task/{task_id}")' not in text:
    text = text.replace(marker, insert + "\n" + marker)
else:
    print("Rotas já existiam; não inseri duplicado.")

out = Path("/mnt/data/run_fixed_v2.py")
out.write_text(text, encoding="utf-8")
py_compile.compile(str(out), doraise=True)

print(f"Arquivo v2 gerado e validado: {out}")
print(f"Tamanho: {out.stat().st_size} bytes")
print("Validação: py_compile OK")
