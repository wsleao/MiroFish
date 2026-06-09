import json
import re
from typing import Any, Dict, List, Optional

from fastapi import Request

# Importa o wrapper C-Level para manter GraphRAG real e agentes executivos.
from backend.run_clevel import app, cors_json, response_ok
from backend.run import (
    SIMULATIONS,
    REPORTS,
    BACKEND_VERSION,
    MODEL,
    BASE_URL,
    utc_now,
    ensure_simulation,
    scheduler_payload,
    limited_llm_chat,
)


EXECUTIVE_REPORT_SECTIONS = [
    "Tese Executiva da Simulação",
    "Diagnóstico Estratégico",
    "Consenso dos Agentes",
    "Divergências e Trade-offs",
    "Cenários Avaliados",
    "Riscos, Sinais de Alerta e Premissas",
    "Recomendação Executiva e Próximos Passos",
]


def report_id_for(simulation_id: str) -> str:
    return f"report_{simulation_id}"


def clean_report_text(value: Any, limit: int = 9000) -> str:
    text = str(value or "")
    text = re.sub(r"\s+", " ", text).strip()
    return text[:limit]


def simulation_context(sim: Dict[str, Any]) -> str:
    project_context = sim.get("project_context") or {}
    pieces = [
        sim.get("context_preview"),
        sim.get("prompt"),
        project_context.get("context_preview"),
        project_context.get("prompt"),
        project_context.get("simulation_requirement"),
    ]
    text = "\n\n".join([str(p) for p in pieces if p])
    return text[:12000]


def actions_by_agent(sim: Dict[str, Any]) -> Dict[str, List[str]]:
    grouped: Dict[str, List[str]] = {}
    for action in sim.get("actions", []):
        name = action.get("agent_name") or action.get("agent") or f"Agente {action.get('agent_id', '')}"
        content = clean_report_text(action.get("content") or action.get("message") or "", 1600)
        if content:
            grouped.setdefault(name, []).append(content)
    return grouped


def build_action_digest(sim: Dict[str, Any], max_items: int = 18) -> str:
    lines: List[str] = []
    for action in sim.get("actions", [])[-max_items:]:
        agent = action.get("agent_name") or f"Agente {action.get('agent_id', '')}"
        round_number = action.get("round") or action.get("round_number") or "?"
        content = clean_report_text(action.get("content") or "", 900)
        if content:
            lines.append(f"[Rodada {round_number}] {agent}: {content}")
    return "\n".join(lines)[:12000]


def agent_list(sim: Dict[str, Any]) -> str:
    agents = sim.get("agents") or []
    names = [a.get("name") for a in agents if a.get("name")]
    return ", ".join(names) if names else "agentes não registrados"


def fallback_executive_sections(simulation_id: str, sim: Dict[str, Any]) -> List[Dict[str, str]]:
    context = clean_report_text(simulation_context(sim), 1400)
    grouped = actions_by_agent(sim)
    agents = agent_list(sim)
    total_actions = len(sim.get("actions", []))
    total_rounds = sim.get("total_rounds") or sim.get("current_round") or 0

    latest_by_agent = []
    for agent, items in grouped.items():
        latest_by_agent.append(f"- **{agent}**: {items[-1][:500]}")
    latest_text = "\n".join(latest_by_agent[:10]) or "Não houve contribuições suficientes dos agentes para consolidar consenso."

    return [
        {
            "title": "Tese Executiva da Simulação",
            "content": (
                f"A simulação `{simulation_id}` executou {total_actions} contribuições em {total_rounds} rodadas com os agentes: {agents}. "
                "O objetivo do relatório não é listar respostas dos agentes, mas consolidar uma leitura executiva sobre a decisão em análise. "
                "Com base no material simulado, a tese deve ser validada contra dados reais de mercado, clientes e indicadores internos antes de virar decisão final."
            ),
        },
        {
            "title": "Diagnóstico Estratégico",
            "content": (
                "O cenário analisado indica uma discussão estratégica sobre oportunidade, posicionamento, risco competitivo e aderência de produto. "
                f"Contexto capturado: {context or 'contexto não preservado integralmente no backend atual.'}"
            ),
        },
        {
            "title": "Consenso dos Agentes",
            "content": (
                "As contribuições dos agentes convergem para a necessidade de transformar a análise em decisão objetiva: problema real, público-alvo, diferencial, riscos, impacto financeiro e plano de execução.\n\n"
                f"{latest_text}"
            ),
        },
        {
            "title": "Divergências e Trade-offs",
            "content": (
                "A simulação deve explicitar os trade-offs, não apenas repetir análises. Pontos que precisam ser comparados: velocidade de lançamento versus qualidade da solução; profundidade analítica versus simplicidade para o usuário; diferenciação competitiva versus custo de implementação; inteligência baseada em dados internos versus dependência de fontes externas."
            ),
        },
        {
            "title": "Cenários Avaliados",
            "content": (
                "**Cenário Defensivo:** acompanhar o movimento e responder apenas com melhorias incrementais. Menor risco, menor diferenciação.\n\n"
                "**Cenário Base:** estruturar uma resposta de produto com dados internos, indicadores comparáveis e relatório executivo. Equilibra velocidade e valor.\n\n"
                "**Cenário Agressivo:** criar plataforma de decisão com inteligência multiagente, comparação, simulação e recomendações. Maior potencial estratégico, maior exigência de dados, governança e execução."
            ),
        },
        {
            "title": "Riscos, Sinais de Alerta e Premissas",
            "content": (
                "Principais riscos: simular sem dados estruturados suficientes; confundir relatório operacional com recomendação executiva; não diferenciar a proposta frente a concorrentes; depender de respostas genéricas da IA; não registrar premissas, evidências e nível de confiança. "
                "Sinais de alerta: baixa aderência das respostas ao prompt, repetição entre agentes, ausência de cenários comparativos e falta de recomendação priorizada."
            ),
        },
        {
            "title": "Recomendação Executiva e Próximos Passos",
            "content": (
                "Recomendação: tratar esta simulação como insumo inicial, mas exigir uma segunda rodada orientada por perguntas executivas e critérios de decisão. Próximos passos: definir hipótese principal, evidências obrigatórias, critérios de avaliação, cenário defensivo/base/agressivo, impacto esperado, riscos e plano de ação. "
                "O relatório final deve terminar com uma decisão recomendada, nível de confiança e ações de 7, 15 e 30 dias."
            ),
        },
    ]


async def generate_llm_executive_sections(simulation_id: str, sim: Dict[str, Any]) -> Optional[List[Dict[str, str]]]:
    context = simulation_context(sim)
    digest = build_action_digest(sim)
    if not digest:
        return None

    prompt = f"""
Você é o Report Agent Executivo do MiroFish. Gere um relatório de resultado de simulação para C-Levels.

REGRAS:
- Não faça relatório operacional.
- Não liste apenas respostas dos agentes.
- Consolide a simulação em decisão executiva.
- Identifique consenso, divergências, trade-offs, cenários, riscos e recomendação.
- Seja direto, crítico e orientado à decisão.
- Responda em JSON válido, lista de objetos com campos title e content.
- Use exatamente estas seções: {EXECUTIVE_REPORT_SECTIONS}

CONTEXTO ORIGINAL:
{context[:6000]}

AÇÕES DOS AGENTES:
{digest[:10000]}
""".strip()

    try:
        raw = await limited_llm_chat(
            messages=[
                {"role": "system", "content": "Você gera sínteses executivas de simulações multiagente. Responda somente JSON válido."},
                {"role": "user", "content": prompt},
            ],
            max_tokens=1800,
            sim=sim,
            reason="executive_report_generation",
        )
        match = re.search(r"\[[\s\S]*\]", raw)
        data = json.loads(match.group(0) if match else raw)
        sections: List[Dict[str, str]] = []
        for item in data:
            title = str(item.get("title") or "").strip()
            content = str(item.get("content") or "").strip()
            if title and content:
                sections.append({"title": title, "content": content})
        if len(sections) >= 4:
            return sections
    except Exception as exc:
        sim.setdefault("logs", []).append({"time": utc_now(), "message": f"Report Agent Executivo usou fallback determinístico: {exc}"})
    return None


async def build_executive_report(simulation_id: str) -> Dict[str, Any]:
    sim = ensure_simulation(simulation_id)
    report_id = report_id_for(simulation_id)
    sections = await generate_llm_executive_sections(simulation_id, sim)
    generation_mode = "llm_executive_synthesis"
    if not sections:
        sections = fallback_executive_sections(simulation_id, sim)
        generation_mode = "deterministic_executive_synthesis"

    report = {
        "report_id": report_id,
        "reportId": report_id,
        "simulation_id": simulation_id,
        "status": "ready",
        "state": "ready",
        "title": "Relatório Executivo MiroFish - Resultado da Simulação",
        "summary": "Síntese executiva da simulação multiagente orientada à decisão, com consenso, trade-offs, cenários, riscos e recomendação.",
        "generation_mode": generation_mode,
        "model": MODEL,
        "base_url": BASE_URL,
        "agents_count": len(sim.get("agents", [])),
        "agents": [a.get("name") for a in sim.get("agents", [])],
        "total_rounds": sim.get("total_rounds") or sim.get("current_round"),
        "total_actions": len(sim.get("actions", [])),
        "sections": sections,
        "scheduler": sim.get("scheduler", scheduler_payload()),
        "generated_at": utc_now(),
        "backend_version": BACKEND_VERSION,
    }
    REPORTS[report_id] = report
    sim["report"] = report
    return report


def build_report_agent_events(report: Dict[str, Any]) -> List[Dict[str, Any]]:
    events: List[Dict[str, Any]] = []
    report_id = report["report_id"]
    simulation_id = report["simulation_id"]
    sections = report.get("sections", [])
    outline = {"title": report.get("title"), "summary": report.get("summary"), "sections": [{"title": s.get("title")} for s in sections]}

    events.append({"timestamp": utc_now(), "action": "report_start", "details": {"report_id": report_id, "simulation_id": simulation_id, "simulation_requirement": "Gerar relatório executivo de resultado da simulação."}})
    events.append({"timestamp": utc_now(), "action": "planning_start", "details": {"message": "Planejando síntese executiva da simulação."}})
    events.append({"timestamp": utc_now(), "action": "planning_complete", "details": {"message": "Estrutura executiva criada.", "outline": outline}})
    for index, section in enumerate(sections, start=1):
        title = section.get("title") or f"Seção {index}"
        content = section.get("content") or "Sem conteúdo consolidado."
        events.append({"timestamp": utc_now(), "action": "section_start", "section_index": index, "section_title": title, "details": {"message": "Gerando seção executiva."}})
        events.append({"timestamp": utc_now(), "action": "section_complete", "section_index": index, "section_title": title, "details": {"content": f"## {title}\n\n{content}"}})
    events.append({"timestamp": utc_now(), "action": "report_complete", "details": {"report_id": report_id, "simulation_id": simulation_id, "message": "Relatório executivo finalizado."}})
    return events


def build_console_lines(report: Dict[str, Any]) -> List[str]:
    return [
        f"[{utc_now()}] Executive Report Agent started for {report['report_id']}",
        f"[{utc_now()}] Simulation loaded: {report['simulation_id']}",
        f"[{utc_now()}] Mode: {report.get('generation_mode')}",
        f"[{utc_now()}] Agents: {report.get('agents_count')}",
        f"[{utc_now()}] Actions: {report.get('total_actions')}",
        f"[{utc_now()}] Sections: {len(report.get('sections', []))}",
        f"[{utc_now()}] Executive synthesis completed",
    ]


@app.middleware("http")
async def executive_report_middleware(request: Request, call_next):
    path = request.url.path
    is_report_path = (
        path == "/api/report/generate"
        or path == "/api/report/generate/status"
        or (path.startswith("/api/report/") and not path.endswith("/chat"))
    )

    if request.method == "OPTIONS" and is_report_path:
        return cors_json({"ok": True})

    if request.method == "POST" and path == "/api/report/generate":
        payload = {}
        try:
            payload = await request.json()
        except Exception:
            try:
                form = await request.form()
                payload = {k: v for k, v in form.multi_items() if not hasattr(v, "filename")}
            except Exception:
                payload = {}
        simulation_id = str(payload.get("simulation_id") or payload.get("simulationId") or next(reversed(SIMULATIONS), "render-project"))
        report = await build_executive_report(simulation_id)
        return cors_json(response_ok({"status": "ready", "state": "ready", "report_id": report["report_id"], "reportId": report["report_id"], "simulation_id": simulation_id, "data": report, "result": report, "report": report}))

    if request.method == "GET" and path == "/api/report/generate/status":
        report_id = request.query_params.get("report_id") or request.query_params.get("reportId") or ""
        simulation_id = report_id.replace("report_", "") if report_id else next(reversed(SIMULATIONS), "render-project")
        report = REPORTS.get(report_id) if report_id else None
        if not report:
            report = await build_executive_report(simulation_id)
        return cors_json(response_ok({"status": "ready", "state": "ready", "report_id": report["report_id"], "data": report, "result": report, "report": report}))

    if request.method == "GET" and path.startswith("/api/report/"):
        parts = path.split("/")
        report_id = parts[3] if len(parts) > 3 else ""
        simulation_id = report_id.replace("report_", "")
        report = REPORTS.get(report_id)
        if not report:
            report = await build_executive_report(simulation_id)

        if path.endswith("/agent-log"):
            from_line = int(request.query_params.get("from_line", "0") or 0)
            events = build_report_agent_events(report)
            sliced = events[max(0, from_line):]
            return cors_json(response_ok({"status": "completed", "report_id": report_id, "data": {"logs": sliced, "from_line": from_line, "total_lines": len(events), "completed": True}, "result": {"logs": sliced}}))

        if path.endswith("/console-log"):
            from_line = int(request.query_params.get("from_line", "0") or 0)
            lines = build_console_lines(report)
            sliced = lines[max(0, from_line):]
            return cors_json(response_ok({"status": "completed", "report_id": report_id, "data": {"logs": sliced, "from_line": from_line, "total_lines": len(lines), "completed": True}, "result": {"logs": sliced}}))

        return cors_json(response_ok({"status": report.get("status", "ready"), "state": report.get("state", "ready"), "report_id": report_id, "data": report, "result": report, "report": report}))

    return await call_next(request)
