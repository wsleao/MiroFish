<template>
  <main class="login-page">
    <section class="hero-panel">
      <div class="brand-mark">NSC</div>
      <p class="eyebrow">Novix Simulation Core</p>
      <h1>Simulações executivas para qualquer segmento.</h1>
      <p class="hero-copy">
        Conecte arquivos, APIs, links, textos, bancos de dados e webhooks para transformar dados dispersos em cenários, riscos, consenso entre agentes e recomendações de decisão.
      </p>
      <div class="feature-grid">
        <span>Multi-fonte</span>
        <span>GraphRAG</span>
        <span>Agentes C-Level</span>
        <span>SaaS-ready</span>
      </div>
    </section>

    <section class="login-card">
      <div class="card-header">
        <p class="eyebrow">Acesso seguro</p>
        <h2>Entrar no NSC</h2>
        <p>Use seu acesso para simulações, histórico e relatórios.</p>
      </div>

      <form @submit.prevent="handleLogin" class="login-form">
        <label>E-mail<input v-model="email" type="email" autocomplete="email" placeholder="admin@nsc.local" required /></label>
        <label>Senha<input v-model="password" type="password" autocomplete="current-password" placeholder="••••••••" required /></label>
        <div v-if="error" class="error-box">{{ error }}</div>
        <button :disabled="loading" type="submit"><span>{{ loading ? 'Validando acesso...' : 'Entrar' }}</span><strong>→</strong></button>
      </form>

      <div class="login-note"><strong>MVP SaaS:</strong> login local de demonstração. A próxima etapa é autenticação persistente por tenant, usuários e permissões no banco.</div>
    </section>
  </main>
</template>

<script setup>
import { ref } from 'vue'
import { useRouter } from 'vue-router'
import { setAuthSession } from '../store/auth'

const router = useRouter()
const email = ref('admin@nsc.local')
const password = ref('')
const loading = ref(false)
const error = ref('')

const handleLogin = async () => {
  loading.value = true
  error.value = ''
  window.setTimeout(() => {
    if (!email.value || password.value.length < 4) {
      error.value = 'Informe um e-mail válido e uma senha com pelo menos 4 caracteres.'
      loading.value = false
      return
    }
    setAuthSession(`nsc-local-${Date.now()}`, {
      id: 'usr_local_owner',
      name: 'Administrador NSC',
      email: email.value,
      tenant_id: 'tenant_personal_default',
      workspace: 'NSC Workspace',
      roles: ['owner']
    })
    loading.value = false
    router.push({ name: 'Home' })
  }, 250)
}
</script>

<style scoped>
.login-page{min-height:100vh;display:grid;grid-template-columns:minmax(0,1.1fr)minmax(360px,520px);background:#0b1020;color:#eef3ff;font-family:Inter,system-ui,sans-serif}.hero-panel{padding:72px;display:flex;flex-direction:column;justify-content:center;background:radial-gradient(circle at 15% 20%,rgba(83,109,254,.32),transparent 28%),radial-gradient(circle at 75% 80%,rgba(0,209,178,.24),transparent 30%),linear-gradient(135deg,#07111f,#111827)}.brand-mark{width:76px;height:76px;border-radius:24px;background:linear-gradient(135deg,#7c9cff,#00d1b2);color:#061020;display:grid;place-items:center;font-weight:900;font-size:24px;box-shadow:0 20px 70px rgba(0,209,178,.25);margin-bottom:32px}.eyebrow{color:#8fb1ff;text-transform:uppercase;letter-spacing:.16em;font-size:12px;font-weight:800}h1{font-size:clamp(42px,6vw,76px);line-height:.95;letter-spacing:-.06em;margin:14px 0 26px;max-width:780px}.hero-copy{color:#b8c2d6;font-size:18px;line-height:1.7;max-width:720px}.feature-grid{margin-top:42px;display:flex;gap:12px;flex-wrap:wrap}.feature-grid span{padding:10px 14px;border:1px solid rgba(255,255,255,.14);border-radius:999px;background:rgba(255,255,255,.06);color:#dce6ff;font-size:13px;font-weight:700}.login-card{margin:auto;width:min(420px,calc(100% - 48px));background:#fff;color:#111827;border-radius:32px;padding:34px;box-shadow:0 28px 90px rgba(0,0,0,.35)}.card-header h2{font-size:32px;margin:8px 0;letter-spacing:-.04em}.card-header p:not(.eyebrow){color:#667085;line-height:1.5}.login-form{margin-top:28px;display:grid;gap:16px}label{display:grid;gap:8px;color:#344054;font-size:13px;font-weight:800}input{width:100%;border:1px solid #d0d5dd;border-radius:14px;padding:14px 16px;font-size:15px;outline:none;transition:.2s}input:focus{border-color:#536dfe;box-shadow:0 0 0 4px rgba(83,109,254,.12)}button{margin-top:8px;border:0;border-radius:16px;background:#111827;color:#fff;padding:15px 18px;display:flex;align-items:center;justify-content:space-between;font-size:15px;font-weight:900;cursor:pointer}button:disabled{opacity:.65;cursor:not-allowed}.error-box{background:#fff1f0;color:#b42318;border:1px solid #fecdca;padding:12px;border-radius:12px;font-size:13px}.login-note{margin-top:22px;padding:14px;background:#f2f4f7;color:#667085;border-radius:16px;font-size:12px;line-height:1.5}@media(max-width:900px){.login-page{grid-template-columns:1fr}.hero-panel{padding:42px 24px;min-height:42vh}.login-card{margin:24px auto 48px}}
</style>
