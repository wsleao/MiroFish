const TOKEN_KEY = 'nsc_auth_token'
const USER_KEY = 'nsc_auth_user'

export function getAuthToken() {
  return localStorage.getItem(TOKEN_KEY) || ''
}

export function getAuthUser() {
  try {
    return JSON.parse(localStorage.getItem(USER_KEY) || 'null')
  } catch (_) {
    return null
  }
}

export function isAuthenticated() {
  return Boolean(getAuthToken())
}

export function setAuthSession(token, user) {
  localStorage.setItem(TOKEN_KEY, token)
  localStorage.setItem(USER_KEY, JSON.stringify(user || {}))
}

export function clearAuthSession() {
  localStorage.removeItem(TOKEN_KEY)
  localStorage.removeItem(USER_KEY)
}

export default {
  getAuthToken,
  getAuthUser,
  isAuthenticated,
  setAuthSession,
  clearAuthSession,
}
