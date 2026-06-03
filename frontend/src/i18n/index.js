import { createI18n } from 'vue-i18n'
import languages from '../../../locales/languages.json'

// Carrega dinamicamente todos os arquivos de tradução da pasta locales
const localeFiles = import.meta.glob('../../../locales/!(languages).json', { eager: true })

const messages = {}
const availableLocales = []

for (const path in localeFiles) {
  const match = path.match(/\/([^/]+)\.json$/)
  if (match) {
    const key = match[1] // Captura o nome do arquivo corretamente (ex: pt, en, zh)
    if (languages[key]) {
      messages[key] = localeFiles[path].default
      availableLocales.push({ key, label: languages[key].label })
    }
  }
}

// Se o usuário já escolheu um idioma, mantém. Se for o primeiro acesso, carrega em Português
const savedLocale = localStorage.getItem('locale') || 'pt'

const i18n = createI18n({
  legacy: false,
  locale: savedLocale,
  fallbackLocale: 'en', // Se faltar alguma palavra em um idioma, usa o inglês como reserva
  messages
})

export { availableLocales }
export default i18n
