<template>
  <div class="nsc-page">
    <aside class="sidebar">
      <div class="brand"><div class="logo">NSC</div><div><strong>Novix Simulation Core</strong><span>Simulation SaaS</span></div></div>
      <nav>
        <button class="active">Nova simulação</button>
        <button @click="scrollToHistory">Últimas simulações</button>
        <button>Fontes de dados</button>
        <button>Agentes</button>
        <button>Configurações</button>
      </nav>
      <div class="note"><strong>Projeto pessoal</strong><span>Independente da SCADIAgro</span></div>
    </aside>

    <main class="content">
      <header class="topbar">
        <div>
          <p class="eyebrow">Decision Simulation Platform</p>
          <h1>Simule decisões com dados de qualquer fonte</h1>
        </div>
        <button class="logout" @click="logout">Sair</button>
      </header>

      <section class="hero">
        <div>
          <span class="pill">NSC Novix Simulation Core</span>
          <h2>Simulação multiagente para qualquer segmento</h2>
          <p>Conecte arquivos, APIs, links, textos, bancos de dados e webhooks para gerar cenários, riscos, consenso entre agentes e recomendações executivas.</p>
        </div>
        <div class="metrics"><div><strong>Multi-fonte</strong><span>entrada flexível</span></div><div><strong>SaaS</strong><span>acesso protegido</span></div><div><strong>GraphRAG</strong><span>contexto explicável</span></div></div>
      </section>

      <section class="grid">
        <form class="card" @submit.prevent="startSimulation">
          <div class="title"><span>01</span><div><h3>Configurar simulação</h3><p>Defina o objetivo e as fontes que alimentarão os agentes.</p></div></div>

          <label>Nome da simulação<input v-model="form.projectName" placeholder="Ex.: Expansão comercial, novo produto, risco financeiro" /></label>
          <label>Objetivo da simulação<textarea v-model="form.requirement" rows="6" placeholder="Descreva a decisão, hipóteses, restrições e perguntas que os agentes devem responder."></textarea></label>

          <div class="tabs">
            <button type="button" v-for="item in sourceTypes" :key="item.id" :class="{active: activeSource === item.id}" @click="activeSource = item.id">{{ item.label }}</button>
          </div>

          <div class="source-box">
            <div v-if="activeSource === 'files'" class="upload" :class="{over: isDragOver}" @dragover.prevent="isDragOver = true" @dragleave.prevent="isDragOver = false" @drop.prevent="handleDrop" @click="triggerFileInput">
              <input ref="fileInput" type="file" multiple accept=".pdf,.md,.txt,.csv,.json,.xlsx" @change="handleFileSelect" hidden />
              <div v-if="!files.length"><strong>Arraste arquivos ou clique para selecionar</strong><span>PDF, TXT, MD, CSV, JSON, XLSX</span></div>
              <div v-else class="file-list"><div v-for="(file, index) in files" :key="index" class="file-item"><span>{{ file.name }}</span><button type="button" @click.stop="removeFile(index)">×</button></div></div>
            </div>

            <label v-else-if="activeSource === 'text'">Texto livre<textarea v-model="sources.text" rows="5" placeholder="Cole texto, briefing, notícia, ata ou transcrição."></textarea></label>
            <label v-else-if="activeSource === 'url'">Link<input v-model="sources.url" placeholder="https://exemplo.com/fonte-de-dados" /></label>
            <div v-else-if="activeSource === 'api'" class="two"><label>Nome da API<input v-model="sources.apiName" placeholder="ERP, CRM, BI..." /></label><label>Base URL<input v-model="sources.apiUrl" placeholder="https://api.exemplo.com" /></label><label>Endpoint<input v-model="sources.apiEndpoint" placeholder="/metrics" /></label><label>Autenticação<input v-model="sources.apiAuth" placeholder="API key, OAuth, Bearer" /></label></div>
            <div v-else-if="activeSource === 'database'" class="two"><label>Banco<input v-model="sources.dbLabel" placeholder="PostgreSQL, SQL Server..." /></label><label>Schema/Dataset<input v-model="sources.dbSchema" placeholder="public, analytics..." /></label><label class="full">Observações<textarea v-model="sources.dbNotes" rows="3" placeholder="Tabelas, regras de acesso e permissões."></textarea></label></div>
            <div v-else-if="activeSource === 'webhook'" class="two"><label>URL do webhook<input v-model="sources.webhookUrl" placeholder="https://app.com/webhook/nsc" /></label><label>Evento<input v-model="sources.webhookEvent" placeholder="pedido.criado, alerta.risco" /></label></div>
          </div>

          <div class="config">
            <label>Agentes<select v-model.number="form.agentCount"><option :value="4">4</option><option :value="6">6</option><option :value="8">8</option></select></label>
            <label>Rodadas<select v-model.number="form.rounds"><option :value="3">3</option><option :value="5">5</option><option :value="8">8</option></select></label>
            <label>Segmento<input v-model="form.segment" placeholder="Qualquer segmento" /></label>
          </div>

          <button class="primary" :disabled="!canSubmit" type="submit">Iniciar simulação <strong>→</strong></button>
        </form>

        <aside class="info">
          <div class="info-card"><h3>Fluxo NSC</h3><ol><li>Conectar fontes</li><li>Gerar ontologia</li><li>Construir GraphRAG</li><li>Simular cenários</li><li>Gerar recomendação</li></ol></div>
          <div class="info-card blue"><h3>SaaS por design</h3><p>Login, histórico, workspace, permissões e multi-tenant serão evoluídos sobre persistência em banco.</p></div>
        </aside>
      </section>

      <section id="history" class="history"><div class="heading"><div><p class="eyebrow">Histórico</p><h2>Últimas simulações</h2></div><span>Consulta rápida das execuções recentes</span></div><HistoryDatabase /></section>
    </main>
  </div>
</template>

<script setup>
import { ref, computed } from 'vue'
import { useRouter } from 'vue-router'
import HistoryDatabase from '../components/HistoryDatabase.vue'
import { setPendingUpload } from '../store/pendingUpload.js'
import { clearAuthSession } from '../store/auth.js'

const router = useRouter()
const form = ref({ projectName: '', requirement: '', agentCount: 6, rounds: 3, segment: '' })
const files = ref([])
const fileInput = ref(null)
const isDragOver = ref(false)
const activeSource = ref('files')
const sourceTypes = [{id:'files', label:'Arquivos'}, {id:'text', label:'Texto'}, {id:'url', label:'Link'}, {id:'api', label:'API'}, {id:'database', label:'Banco'}, {id:'webhook', label:'Webhook'}]
const sources = ref({ text: '', url: '', apiName: '', apiUrl: '', apiEndpoint: '', apiAuth: '', dbLabel: '', dbSchema: '', dbNotes: '', webhookUrl: '', webhookEvent: '' })

const buildDataSources = () => {
  const list = []
  if (files.value.length) list.push({ type: 'files', names: files.value.map(f => f.name), count: files.value.length })
  if (sources.value.text.trim()) list.push({ type: 'text', content: sources.value.text.trim() })
  if (sources.value.url.trim()) list.push({ type: 'url', url: sources.value.url.trim() })
  if (sources.value.apiUrl.trim() || sources.value.apiEndpoint.trim()) list.push({ type: 'api', name: sources.value.apiName, baseUrl: sources.value.apiUrl, endpoint: sources.value.apiEndpoint, authHint: sources.value.apiAuth })
  if (sources.value.dbLabel.trim() || sources.value.dbSchema.trim()) list.push({ type: 'database', label: sources.value.dbLabel, schema: sources.value.dbSchema, notes: sources.value.dbNotes })
  if (sources.value.webhookUrl.trim()) list.push({ type: 'webhook', url: sources.value.webhookUrl, event: sources.value.webhookEvent })
  return list
}
const canSubmit = computed(() => form.value.requirement.trim() && buildDataSources().length > 0)
const triggerFileInput = () => fileInput.value?.click()
const handleFileSelect = e => addFiles(Array.from(e.target.files || []))
const handleDrop = e => { isDragOver.value = false; addFiles(Array.from(e.dataTransfer.files || [])) }
const addFiles = newFiles => files.value.push(...newFiles)
const removeFile = index => files.value.splice(index, 1)
const startSimulation = () => {
  const dataSources = buildDataSources()
  setPendingUpload(files.value, form.value.requirement, { projectName: form.value.projectName || 'Nova simulação NSC', dataSources, simulationConfig: { agentCount: form.value.agentCount, rounds: form.value.rounds, segment: form.value.segment, sourceMode: dataSources.map(s => s.type).join(',') } })
  router.push({ name: 'Process', params: { projectId: 'new' } })
}
const scrollToHistory = () => document.getElementById('history')?.scrollIntoView({ behavior: 'smooth' })
const logout = () => { clearAuthSession(); router.push({ name: 'Login' }) }
</script>

<style scoped>
.nsc-page{min-height:100vh;display:grid;grid-template-columns:280px 1fr;background:#f6f8fb;color:#111827;font-family:Inter,system-ui,sans-serif}.sidebar{position:sticky;top:0;height:100vh;background:#0b1020;color:#fff;padding:24px;display:flex;flex-direction:column}.brand{display:flex;gap:14px;align-items:center}.logo{width:48px;height:48px;border-radius:16px;background:linear-gradient(135deg,#7c9cff,#00d1b2);color:#07111f;display:grid;place-items:center;font-weight:950}.brand strong{display:block}.brand span,.note span{color:#98a2b3;font-size:12px}nav{display:grid;gap:8px;margin-top:38px}nav button{border:0;text-align:left;padding:13px 14px;border-radius:14px;color:#cbd5e1;background:transparent;font-weight:750;cursor:pointer}nav button.active,nav button:hover{background:rgba(255,255,255,.09);color:#fff}.note{margin-top:auto;padding:16px;border-radius:18px;background:rgba(255,255,255,.08);display:grid;gap:4px}.content{padding:28px;overflow-x:hidden}.topbar{display:flex;justify-content:space-between;gap:24px;align-items:center;margin-bottom:24px}.eyebrow{color:#536dfe;text-transform:uppercase;letter-spacing:.14em;font-weight:900;font-size:12px}h1{font-size:clamp(32px,4vw,54px);letter-spacing:-.06em;margin-top:6px}.logout{border:1px solid #d0d5dd;background:#fff;border-radius:12px;padding:11px 14px;font-weight:800;cursor:pointer;color:#b42318}.hero{border-radius:32px;padding:34px;background:linear-gradient(135deg,#111827,#172554);color:#fff;display:grid;grid-template-columns:1.2fr .8fr;gap:28px;box-shadow:0 24px 80px rgba(17,24,39,.18)}.pill{display:inline-flex;padding:8px 12px;background:rgba(255,255,255,.12);border-radius:999px;color:#bfdbfe;font-size:12px;font-weight:900}.hero h2{font-size:44px;letter-spacing:-.05em;margin:14px 0}.hero p{color:#cbd5e1;line-height:1.65}.metrics{display:grid;grid-template-columns:repeat(3,1fr);gap:12px;align-content:end}.metrics div{background:rgba(255,255,255,.1);border:1px solid rgba(255,255,255,.12);border-radius:22px;padding:18px}.metrics strong{display:block}.metrics span{color:#cbd5e1;font-size:12px}.grid{display:grid;grid-template-columns:minmax(0,1fr)340px;gap:22px;margin-top:22px}.card,.info-card,.history{background:#fff;border:1px solid #e4e7ec;border-radius:28px;padding:24px;box-shadow:0 14px 40px rgba(16,24,40,.05)}.title{display:flex;gap:16px;margin-bottom:22px}.title span{width:42px;height:42px;border-radius:14px;background:#eef4ff;color:#3538cd;display:grid;place-items:center;font-weight:950}.title h3,.info-card h3,.heading h2{margin:0 0 6px;font-size:24px;letter-spacing:-.04em}.title p,.info-card p,.heading span{color:#667085;line-height:1.5}label{display:grid;gap:8px;font-size:13px;color:#344054;font-weight:850;margin-bottom:16px}input,textarea,select{width:100%;border:1px solid #d0d5dd;border-radius:14px;padding:13px 14px;font:inherit;outline:none;background:#fff}textarea{resize:vertical}input:focus,textarea:focus,select:focus{border-color:#536dfe;box-shadow:0 0 0 4px rgba(83,109,254,.12)}.tabs{display:flex;gap:8px;flex-wrap:wrap;margin:18px 0}.tabs button{border:1px solid #e4e7ec;background:#fff;border-radius:999px;padding:10px 13px;font-weight:850;cursor:pointer;color:#475467}.tabs button.active{background:#111827;color:#fff;border-color:#111827}.source-box{border:1px dashed #cbd5e1;background:#f8fafc;border-radius:22px;padding:18px;min-height:150px}.upload{min-height:132px;display:grid;place-items:center;text-align:center;color:#667085;cursor:pointer}.upload strong{display:block;color:#111827;margin-bottom:6px}.over{background:#eef4ff;border-radius:18px}.file-list{width:100%;display:grid;gap:8px}.file-item{display:flex;justify-content:space-between;align-items:center;background:#fff;border:1px solid #e4e7ec;border-radius:12px;padding:10px 12px}.file-item button{border:0;background:#fee4e2;color:#b42318;border-radius:8px;width:28px;height:28px;cursor:pointer}.two,.config{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:12px}.two .full{grid-column:1/-1}.config{grid-template-columns:160px 140px 1fr;margin:18px 0}.primary{width:100%;border:0;background:#111827;color:#fff;border-radius:18px;padding:16px 18px;display:flex;justify-content:space-between;font-weight:950;font-size:15px;cursor:pointer}.primary:disabled{opacity:.55;cursor:not-allowed}.info{display:grid;gap:16px;align-content:start}.info-card ol{margin:12px 0 0 20px;color:#475467;line-height:2;font-weight:700}.blue{background:#eef4ff;border-color:#c7d7fe}.history{margin-top:22px}.heading{display:flex;justify-content:space-between;align-items:end;margin-bottom:18px}@media(max-width:1100px){.nsc-page{grid-template-columns:1fr}.sidebar{position:static;height:auto}.grid,.hero{grid-template-columns:1fr}.metrics{grid-template-columns:1fr}}@media(max-width:720px){.content{padding:18px}.topbar,.heading{flex-direction:column;align-items:flex-start}.two,.config{grid-template-columns:1fr}}
</style>
