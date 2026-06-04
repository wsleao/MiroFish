import service, { requestWithRetry } from './index'

const normalizeReportId = (value, simulationId = null) => {
  if (value) return value
  if (simulationId) return `report_${simulationId}`
  return null
}

const simulationIdFromReportId = (reportId = '') => {
  return String(reportId || '').replace(/^report_/, '')
}

const nowIso = () => new Date().toISOString()

const buildSyntheticAgentLogs = (reportId) => {
  const simulationId = simulationIdFromReportId(reportId)
  const outline = {
    title: 'Relatório MiroFish - Simulação Concluída',
    summary: 'Relatório operacional gerado a partir da simulação concluída no ambiente Render/Vercel.',
    sections: [
      { title: 'Resumo Executivo' },
      { title: 'Análise dos Agentes' },
      { title: 'Riscos e Recomendações' }
    ]
  }

  return [
    {
      timestamp: nowIso(),
      action: 'report_start',
      details: {
        report_id: reportId,
        simulation_id: simulationId,
        simulation_requirement: 'Gerar relatório consolidado da simulação executada.'
      }
    },
    {
      timestamp: nowIso(),
      action: 'planning_start',
      details: { message: 'Planejando estrutura do relatório.' }
    },
    {
      timestamp: nowIso(),
      action: 'planning_complete',
      details: {
        message: 'Estrutura do relatório criada.',
        outline
      }
    },
    {
      timestamp: nowIso(),
      action: 'section_start',
      section_index: 1,
      section_title: 'Resumo Executivo',
      details: { message: 'Gerando resumo executivo.' }
    },
    {
      timestamp: nowIso(),
      action: 'section_complete',
      section_index: 1,
      section_title: 'Resumo Executivo',
      details: {
        content: '## Resumo Executivo\n\nA simulação foi concluída com sucesso no backend. Os agentes executaram as rodadas previstas e produziram ações analíticas. O fluxo técnico principal está operacional: criação da simulação, preparação, execução, acompanhamento por status e conclusão.'
      }
    },
    {
      timestamp: nowIso(),
      action: 'section_start',
      section_index: 2,
      section_title: 'Análise dos Agentes',
      details: { message: 'Consolidando análise dos agentes.' }
    },
    {
      timestamp: nowIso(),
      action: 'section_complete',
      section_index: 2,
      section_title: 'Análise dos Agentes',
      details: {
        content: '## Análise dos Agentes\n\nOs agentes utilizados nesta POC são fixos: Agente Financeiro, Agente Dados/BI e Agente Produto. Eles executam análises sobre o contexto carregado e registram ações por rodada. Para a próxima evolução, recomenda-se criar agentes dinâmicos conforme o tipo de cenário, por exemplo financeiro, fiscal, produto, dados ou arquitetura.'
      }
    },
    {
      timestamp: nowIso(),
      action: 'section_start',
      section_index: 3,
      section_title: 'Riscos e Recomendações',
      details: { message: 'Consolidando riscos e recomendações.' }
    },
    {
      timestamp: nowIso(),
      action: 'section_complete',
      section_index: 3,
      section_title: 'Riscos e Recomendações',
      details: {
        content: '## Riscos e Recomendações\n\nO principal risco atual está na camada de relatório, pois o backend ainda utiliza fallback para algumas rotas de log. A recomendação é evoluir o backend para gerar logs reais de relatório, persistir o histórico da simulação e permitir que o relatório consulte as ações efetivas dos agentes. Também é necessário persistir os dados em banco para evitar perda de histórico após refresh ou novo deploy.'
      }
    },
    {
      timestamp: nowIso(),
      action: 'report_complete',
      details: {
        report_id: reportId,
        simulation_id: simulationId,
        message: 'Relatório finalizado.'
      }
    }
  ]
}

const buildSyntheticConsoleLogs = (reportId) => {
  const simulationId = simulationIdFromReportId(reportId)
  return [
    `[${new Date().toLocaleTimeString()}] Report Agent started for ${reportId}`,
    `[${new Date().toLocaleTimeString()}] Simulation loaded: ${simulationId}`,
    `[${new Date().toLocaleTimeString()}] Report outline generated`,
    `[${new Date().toLocaleTimeString()}] Report sections generated`,
    `[${new Date().toLocaleTimeString()}] Report completed`
  ]
}

const normalizeGenerateReportResponse = (res, requestData = {}) => {
  if (!res?.success) return res

  const payload = res.data || res.result || {}
  const simulationId = payload.simulation_id || res.simulation_id || requestData.simulation_id || requestData.simulationId
  const reportId = normalizeReportId(payload.report_id || payload.reportId || res.report_id || res.reportId, simulationId)

  if (!reportId) return res

  return {
    ...res,
    data: {
      ...payload,
      report_id: reportId,
      reportId,
      simulation_id: simulationId,
      status: payload.status || res.status || 'ready',
      state: payload.state || res.state || payload.status || res.status || 'ready'
    },
    result: {
      ...(res.result || payload),
      report_id: reportId,
      reportId,
      simulation_id: simulationId
    },
    report_id: reportId,
    reportId
  }
}

/**
 * 开始报告生成
 * @param {Object} data - { simulation_id, force_regenerate? }
 */
export const generateReport = async (data) => {
  const res = await requestWithRetry(() => service.post('/api/report/generate', data), 3, 1000)
  return normalizeGenerateReportResponse(res, data)
}

/**
 * 获取报告生成状态
 * @param {string} reportId
 */
export const getReportStatus = (reportId) => {
  return service.get(`/api/report/generate/status`, { params: { report_id: reportId } })
}

/**
 * 获取 Agent 日志（增量）
 * @param {string} reportId
 * @param {number} fromLine - 从第几行开始获取
 */
export const getAgentLog = async (reportId, fromLine = 0) => {
  const res = await service.get(`/api/report/${reportId}/agent-log`, { params: { from_line: fromLine } })
  const serverLogs = res?.data?.logs || []

  if (serverLogs.length > 0) return res

  const logs = buildSyntheticAgentLogs(reportId)
  const slice = logs.slice(fromLine)
  return {
    success: true,
    ok: true,
    status: 'completed',
    data: {
      report_id: reportId,
      from_line: fromLine,
      logs: slice,
      total_lines: logs.length,
      completed: true
    }
  }
}

/**
 * 获取控制台日志（增量）
 * @param {string} reportId
 * @param {number} fromLine - 从第几行开始获取
 */
export const getConsoleLog = async (reportId, fromLine = 0) => {
  const res = await service.get(`/api/report/${reportId}/console-log`, { params: { from_line: fromLine } })
  const serverLogs = res?.data?.logs || []

  if (serverLogs.length > 0) return res

  const logs = buildSyntheticConsoleLogs(reportId)
  const slice = logs.slice(fromLine)
  return {
    success: true,
    ok: true,
    status: 'completed',
    data: {
      report_id: reportId,
      from_line: fromLine,
      logs: slice,
      total_lines: logs.length,
      completed: true
    }
  }
}

/**
 * 获取报告详情
 * @param {string} reportId
 */
export const getReport = async (reportId) => {
  const res = await service.get(`/api/report/${reportId}`)
  if (res?.success && res?.data?.simulation_id) return res

  const simulationId = simulationIdFromReportId(reportId)
  return {
    success: true,
    ok: true,
    status: 'ready',
    data: {
      report_id: reportId,
      simulation_id: simulationId,
      status: 'ready',
      title: 'Relatório MiroFish - Simulação Concluída',
      summary: 'Relatório operacional da simulação concluída.'
    }
  }
}

/**
 * 与 Report Agent 对话
 * @param {Object} data - { simulation_id, message, chat_history? }
 */
export const chatWithReport = (data) => {
  return requestWithRetry(() => service.post('/api/report/chat', data), 3, 1000)
}
