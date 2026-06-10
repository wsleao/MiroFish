/**
 * Armazena temporariamente a configuração da simulação antes de abrir o processo.
 * NSC aceita múltiplas fontes de dados: arquivos, texto, links, APIs, banco e webhooks.
 */
import { reactive } from 'vue'

const state = reactive({
  files: [],
  simulationRequirement: '',
  projectName: '',
  dataSources: [],
  simulationConfig: {},
  isPending: false
})

function buildSyntheticSourceText(requirement, options = {}) {
  return [
    'NSC Novix Simulation Core - contexto multi-fonte',
    `Projeto: ${options.projectName || 'Nova simulação NSC'}`,
    `Objetivo: ${requirement || ''}`,
    `Fontes: ${JSON.stringify(options.dataSources || [])}`,
    `Configuração: ${JSON.stringify(options.simulationConfig || {})}`
  ].join('\n')
}

export function setPendingUpload(files, requirement, options = {}) {
  const incomingFiles = files || []
  state.files = incomingFiles.length ? incomingFiles : [buildSyntheticSourceText(requirement, options)]
  state.simulationRequirement = requirement || ''
  state.projectName = options.projectName || 'Nova simulação NSC'
  state.dataSources = options.dataSources || []
  state.simulationConfig = options.simulationConfig || {}
  state.isPending = true
}

export function getPendingUpload() {
  return {
    files: state.files,
    simulationRequirement: state.simulationRequirement,
    projectName: state.projectName,
    dataSources: state.dataSources,
    simulationConfig: state.simulationConfig,
    isPending: state.isPending
  }
}

export function clearPendingUpload() {
  state.files = []
  state.simulationRequirement = ''
  state.projectName = ''
  state.dataSources = []
  state.simulationConfig = {}
  state.isPending = false
}

export default state
