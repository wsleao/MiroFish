import service, { requestWithRetry } from './index'

const summaryFromConfig = (config = {}) => ({
  total_agents: config.agent_configs?.length || config.agents?.length || config.personas?.length || config.total_agents || 0,
  simulation_hours: config.time_config?.total_simulation_hours || config.rounds || 0,
  initial_posts_count: config.event_config?.initial_posts?.length || 0,
  hot_topics_count: config.event_config?.hot_topics?.length || 0,
  has_twitter_config: Boolean(config.twitter_config),
  has_reddit_config: Boolean(config.reddit_config),
  generated_at: config.generated_at,
  llm_model: config.llm_model || config.model || config.mode
})

const normalizeRealtimeConfig = (res, simulationId) => {
  if (!res?.success) return res

  const payload = res.data || {}

  if (payload.config_generated && payload.config) {
    return res
  }

  const config = res.config || payload
  const looksLikeConfig = Boolean(
    res.config_generated ||
    payload.config_generated ||
    config.time_config ||
    config.agent_configs ||
    config.agents ||
    config.personas
  )

  if (!looksLikeConfig) return res

  return {
    ...res,
    data: {
      simulation_id: config.simulation_id || res.simulation_id || simulationId,
      status: 'completed',
      generation_stage: 'completed',
      is_generating: false,
      config_generated: true,
      config_ready: true,
      ready: true,
      done: true,
      summary: res.summary || payload.summary || summaryFromConfig(config),
      config
    }
  }
}

export const createSimulation = (data) => {
  const payload = { ...data, enable_twitter: false, enable_reddit: false }
  return requestWithRetry(() => service.post('/api/simulation/create', payload), 3, 1000)
}

export const prepareSimulation = (data) => requestWithRetry(() => service.post('/api/simulation/prepare', data), 3, 1000)
export const getPrepareStatus = (data) => service.post('/api/simulation/prepare/status', data)
export const getSimulation = (simulationId) => service.get(`/api/simulation/${simulationId}`)

export const getSimulationProfiles = (simulationId, platform = null) => {
  const options = platform ? { params: { platform } } : undefined
  return service.get(`/api/simulation/${simulationId}/profiles`, options)
}

export const getSimulationProfilesRealtime = (simulationId) => {
  return service.get(`/api/simulation/${simulationId}/profiles/realtime`)
}

export const getSimulationConfig = (simulationId) => service.get(`/api/simulation/${simulationId}/config`)

export const getSimulationConfigRealtime = async (simulationId) => {
  const res = await service.get(`/api/simulation/${simulationId}/config/realtime`)
  return normalizeRealtimeConfig(res, simulationId)
}

export const listSimulations = (projectId) => {
  const params = projectId ? { project_id: projectId } : {}
  return service.get('/api/simulation/list', { params })
}

export const startSimulation = (data) => requestWithRetry(() => service.post('/api/simulation/start', data), 3, 1000)
export const stopSimulation = (data) => service.post('/api/simulation/stop', data)
export const getRunStatus = (simulationId) => service.get(`/api/simulation/${simulationId}/run-status`)
export const getRunStatusDetail = (simulationId) => service.get(`/api/simulation/${simulationId}/run-status/detail`)

export const getSimulationPosts = (simulationId, platform = 'internal', limit = 50, offset = 0) => {
  return service.get(`/api/simulation/${simulationId}/posts`, { params: { platform, limit, offset } })
}

export const getSimulationTimeline = (simulationId, startRound = 0, endRound = null) => {
  const params = { start_round: startRound }
  if (endRound !== null) params.end_round = endRound
  return service.get(`/api/simulation/${simulationId}/timeline`, { params })
}

export const getAgentStats = (simulationId) => service.get(`/api/simulation/${simulationId}/agent-stats`)
export const getSimulationActions = (simulationId, params = {}) => service.get(`/api/simulation/${simulationId}/actions`, { params })
export const closeSimulationEnv = (data) => service.post('/api/simulation/close-env', data)
export const getEnvStatus = (data) => service.post('/api/simulation/env-status', data)
export const interviewAgents = (data) => requestWithRetry(() => service.post('/api/simulation/interview/batch', data), 3, 1000)
export const getSimulationHistory = (limit = 20) => service.get('/api/simulation/history', { params: { limit } })
