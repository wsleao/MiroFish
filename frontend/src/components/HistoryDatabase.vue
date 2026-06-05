<template>
  <section class="history-database">
    <div class="history-header">
      <div>
        <div class="eyebrow">Simulation History</div>
        <h2>Últimas simulações</h2>
        <p>Pesquise, reabra e nomeie simulações para facilitar consultas futuras.</p>
      </div>
      <button class="refresh-btn" @click="loadHistory" :disabled="loading">
        {{ loading ? 'Atualizando...' : 'Atualizar' }}
      </button>
    </div>

    <div class="history-toolbar">
      <input
        v-model="searchTerm"
        class="search-input"
        type="text"
        placeholder="Pesquisar por nome, ID, tag, categoria ou conteúdo do cenário..."
        @keyup.enter="loadHistory"
      />
      <button class="search-btn" @click="loadHistory">Pesquisar</button>
      <button v-if="searchTerm" class="clear-btn" @click="clearSearch">Limpar</button>
    </div>

    <div class="naming-guide">
      <strong>Padrão de nome automático:</strong>
      <span>AAAAMMDD | Categoria | Objetivo resumido | SIM-XXXXXX</span>
    </div>

    <div v-if="loading" class="state-box">Carregando histórico...</div>
    <div v-else-if="projects.length === 0" class="state-box empty">Nenhuma simulação encontrada.</div>

    <div v-else class="history-grid">
      <article v-for="project in projects" :key="project.simulation_id" class="history-card">
        <div class="card-topline">
          <span class="short-code">{{ project.short_code || formatSimulationId(project.simulation_id) }}</span>
          <span class="status-pill" :class="getProgressClass(project)">{{ getStatusLabel(project) }}</span>
        </div>

        <h3 class="simulation-name">{{ project.simulation_name || project.display_name || getSimulationTitle(project.simulation_requirement) }}</h3>

        <div class="meta-row">
          <span>{{ project.category || 'Cenário Geral' }}</span>
          <span>{{ formatDate(project.created_at) }} {{ formatTime(project.created_at) }}</span>
        </div>

        <p class="simulation-desc">{{ truncateText(project.simulation_requirement || project.context_preview, 180) }}</p>

        <div class="tag-list" v-if="project.tags && project.tags.length">
          <span v-for="tag in project.tags.slice(0, 6)" :key="tag" class="tag">{{ tag }}</span>
        </div>

        <div class="files-list" v-if="project.files && project.files.length">
          <span v-for="file in project.files.slice(0, 2)" :key="file.filename" class="file-chip">
            {{ file.filename }}
          </span>
          <span v-if="project.files.length > 2" class="file-chip">+{{ project.files.length - 2 }}</span>
        </div>

        <div class="rename-box" v-if="editingId === project.simulation_id">
          <input v-model="editingName" class="rename-input" placeholder="Nome da simulação" />
          <textarea v-model="editingNotes" class="notes-input" placeholder="Observações para pesquisa futura"></textarea>
          <input v-model="editingTags" class="rename-input" placeholder="Tags separadas por vírgula" />
          <div class="rename-actions">
            <button @click="saveMetadata(project)" class="primary-action">Salvar</button>
            <button @click="cancelEdit" class="secondary-action">Cancelar</button>
          </div>
        </div>

        <div class="card-actions" v-else>
          <button @click="openSimulation(project)">Simulação</button>
          <button @click="openReport(project)" :disabled="!project.report_id">Relatório</button>
          <button @click="startEdit(project)">Renomear</button>
        </div>
      </article>
    </div>
  </section>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import { getSimulationHistory, updateSimulationMetadata } from '../api/simulation'

const router = useRouter()

const projects = ref([])
const loading = ref(false)
const searchTerm = ref('')
const editingId = ref(null)
const editingName = ref('')
const editingNotes = ref('')
const editingTags = ref('')

const loadHistory = async () => {
  try {
    loading.value = true
    const response = await getSimulationHistory(50, searchTerm.value)
    if (response.success) {
      projects.value = response.data || []
    } else {
      projects.value = []
    }
  } catch (error) {
    console.error('Falha ao carregar histórico:', error)
    projects.value = []
  } finally {
    loading.value = false
  }
}

const clearSearch = () => {
  searchTerm.value = ''
  loadHistory()
}

const startEdit = (project) => {
  editingId.value = project.simulation_id
  editingName.value = project.simulation_name || project.display_name || ''
  editingNotes.value = project.notes || ''
  editingTags.value = Array.isArray(project.tags) ? project.tags.join(', ') : ''
}

const cancelEdit = () => {
  editingId.value = null
  editingName.value = ''
  editingNotes.value = ''
  editingTags.value = ''
}

const saveMetadata = async (project) => {
  const tags = editingTags.value
    .split(',')
    .map(tag => tag.trim())
    .filter(Boolean)

  await updateSimulationMetadata(project.simulation_id, {
    simulation_name: editingName.value,
    notes: editingNotes.value,
    tags
  })

  cancelEdit()
  await loadHistory()
}

const openSimulation = (project) => {
  router.push({ name: 'Simulation', params: { simulationId: project.simulation_id } })
}

const openReport = (project) => {
  const reportId = project.report_id || `report_${project.simulation_id}`
  router.push({ name: 'Report', params: { reportId } })
}

const formatDate = (dateStr) => {
  if (!dateStr) return ''
  try {
    return new Date(dateStr).toISOString().slice(0, 10)
  } catch {
    return String(dateStr).slice(0, 10)
  }
}

const formatTime = (dateStr) => {
  if (!dateStr) return ''
  try {
    const date = new Date(dateStr)
    return `${String(date.getHours()).padStart(2, '0')}:${String(date.getMinutes()).padStart(2, '0')}`
  } catch {
    return ''
  }
}

const truncateText = (text, maxLength) => {
  if (!text) return 'Sem descrição disponível.'
  return text.length > maxLength ? `${text.slice(0, maxLength)}...` : text
}

const getSimulationTitle = (requirement) => {
  if (!requirement) return 'Simulação sem título'
  const title = requirement.replace(/\s+/g, ' ').trim().slice(0, 80)
  return requirement.length > 80 ? `${title}...` : title
}

const formatSimulationId = (simulationId) => {
  if (!simulationId) return 'SIM-UNKNOWN'
  return `SIM-${simulationId.replace('sim_', '').slice(0, 6).toUpperCase()}`
}

const getProgressClass = (simulation) => {
  if (simulation.status === 'completed' || simulation.current_round >= simulation.total_rounds) return 'completed'
  if (simulation.status === 'running') return 'running'
  if (simulation.status === 'failed') return 'failed'
  return 'created'
}

const getStatusLabel = (simulation) => {
  if (simulation.status === 'completed' || simulation.current_round >= simulation.total_rounds) return 'Concluída'
  if (simulation.status === 'running') return 'Rodando'
  if (simulation.status === 'failed') return 'Erro'
  return 'Criada'
}

onMounted(loadHistory)
</script>

<style scoped>
.history-database {
  margin-top: 80px;
  padding: 32px;
  border: 1px solid #e6e6e6;
  background: #fff;
}

.history-header {
  display: flex;
  justify-content: space-between;
  gap: 24px;
  align-items: flex-start;
  margin-bottom: 24px;
}

.eyebrow {
  font-family: 'JetBrains Mono', monospace;
  text-transform: uppercase;
  font-size: 0.75rem;
  color: #ff4500;
  font-weight: 800;
  margin-bottom: 8px;
}

.history-header h2 {
  font-size: 2rem;
  margin: 0 0 8px;
  color: #111;
}

.history-header p {
  margin: 0;
  color: #666;
}

.refresh-btn,
.search-btn,
.clear-btn,
.card-actions button,
.primary-action,
.secondary-action {
  border: 1px solid #111;
  background: #111;
  color: #fff;
  padding: 10px 14px;
  cursor: pointer;
  font-weight: 700;
}

.clear-btn,
.secondary-action,
.card-actions button:nth-child(3) {
  background: #fff;
  color: #111;
}

button:disabled {
  opacity: 0.4;
  cursor: not-allowed;
}

.history-toolbar {
  display: flex;
  gap: 12px;
  margin-bottom: 16px;
}

.search-input {
  flex: 1;
  border: 1px solid #d8d8d8;
  padding: 12px 14px;
  font-size: 0.95rem;
}

.naming-guide {
  background: #f7f7f7;
  border: 1px solid #eee;
  padding: 12px 14px;
  margin-bottom: 24px;
  color: #444;
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
}

.history-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(320px, 1fr));
  gap: 18px;
}

.history-card {
  border: 1px solid #e3e3e3;
  padding: 18px;
  background: #fff;
  box-shadow: 0 8px 22px rgba(0,0,0,0.04);
}

.card-topline,
.meta-row,
.card-actions,
.rename-actions {
  display: flex;
  justify-content: space-between;
  gap: 10px;
  align-items: center;
}

.short-code {
  font-family: 'JetBrains Mono', monospace;
  font-size: 0.8rem;
  color: #ff4500;
  font-weight: 800;
}

.status-pill {
  font-size: 0.75rem;
  padding: 4px 8px;
  border-radius: 999px;
  background: #eee;
}

.status-pill.completed { background: #e8f7ed; color: #167a38; }
.status-pill.running { background: #fff5df; color: #9a6500; }
.status-pill.failed { background: #fde8e8; color: #b00020; }

.simulation-name {
  margin: 14px 0 10px;
  font-size: 1.05rem;
  line-height: 1.35;
  color: #111;
}

.meta-row {
  color: #777;
  font-size: 0.82rem;
  margin-bottom: 12px;
}

.simulation-desc {
  color: #333;
  line-height: 1.55;
  min-height: 76px;
}

.tag-list,
.files-list {
  display: flex;
  gap: 6px;
  flex-wrap: wrap;
  margin: 12px 0;
}

.tag,
.file-chip {
  font-size: 0.75rem;
  background: #f0f0f0;
  padding: 4px 8px;
  border-radius: 4px;
  color: #444;
}

.file-chip {
  background: #fff3ed;
  color: #b64000;
}

.card-actions {
  margin-top: 16px;
}

.card-actions button {
  flex: 1;
  padding: 9px 10px;
  font-size: 0.82rem;
}

.rename-box {
  border-top: 1px solid #eee;
  padding-top: 12px;
  margin-top: 12px;
}

.rename-input,
.notes-input {
  width: 100%;
  border: 1px solid #d8d8d8;
  padding: 10px;
  margin-bottom: 8px;
}

.notes-input {
  min-height: 70px;
  resize: vertical;
}

.state-box {
  border: 1px dashed #ddd;
  padding: 28px;
  text-align: center;
  color: #666;
}

.empty {
  background: #fafafa;
}
</style>
