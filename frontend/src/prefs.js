import { useSyncExternalStore } from 'react'
import { fetchJSON } from './hooks/useApi'

/**
 * 显示偏好(账户页 → 显示设置)单例 store + 主题应用。
 *
 * 数据流: 登录成功 → initPrefs() 拉 /api/auth/preferences 落 state → apply() 写
 * documentElement 的 data-theme/data-updown → CSS 变量层叠换肤(全站组件零改动)。
 * 切换偏好 → setPrefs() 乐观更新 + apply() + PUT 持久化 + 派发两路事件:
 *   licai:prefs-changed  任何偏好变化(App bump state 全树重渲染, helpers 读单例生效)
 *   licai:theme-changed  仅"解析后主题或涨跌向"变化(图表 canvas 重绘用, useThemeTick)
 *
 * data-theme 只落解析结果 light|dark('跟随系统'在 matchMedia 回调里实时重解析);
 * data-updown 落 red-up|green-up, index.css 四象限(浅/深 × 红/绿涨)整块交换变量。
 */

export const PREFS_EVENT = 'licai:prefs-changed'
export const THEME_EVENT = 'licai:theme-changed'

const DARK_MEDIA = '(prefers-color-scheme: dark)'

// 未登录默认 = 既有显示口径: 浅色 / ≥1万显万 / 红涨绿跌
let state = { theme: 'light', wanUnit: true, updownRed: true }
const listeners = new Set()
let media = null            // system 模式下的 matchMedia 实例(惰性建)
let systemDark = false
let lastResolved = ''       // 上次 apply 的解析结果, 判断要不要发 THEME_EVENT

function resolveTheme(theme) {
  if (theme === 'system') {
    systemDark = typeof window !== 'undefined' && window.matchMedia?.(DARK_MEDIA).matches
    return systemDark ? 'dark' : 'light'
  }
  return theme === 'dark' ? 'dark' : 'light'
}

function apply() {
  if (typeof document === 'undefined') return
  const el = document.documentElement
  const resolved = resolveTheme(state.theme)
  el.dataset.theme = resolved
  el.dataset.updown = state.updownRed ? 'red-up' : 'green-up'
  const signature = `${resolved}|${state.updownRed ? 'r' : 'g'}`
  if (lastResolved && lastResolved !== signature) {
    window.dispatchEvent(new CustomEvent(THEME_EVENT))
  }
  lastResolved = signature
}

// system 模式: 用户改系统主题时实时跟随(仅 pref 是 system 时才有意义, 但监听常驻成本低)
function onSystemChange() {
  if (state.theme !== 'system') return
  apply()
}

function ensureMedia() {
  if (media || typeof window === 'undefined' || !window.matchMedia) return
  media = window.matchMedia(DARK_MEDIA)
  systemDark = media.matches
  media.addEventListener?.('change', onSystemChange)
}

function emit() {
  for (const fn of listeners) {
    try { fn() } catch { /* 订阅者异常不相互影响 */ }
  }
}

export function getPrefs() { return state }

export function subscribePrefs(fn) {
  listeners.add(fn)
  return () => listeners.delete(fn)
}

function merge(next) {
  state = { ...state, ...next }
  apply()
  emit()
  window.dispatchEvent(new CustomEvent(PREFS_EVENT))
}

/** 模块加载即应用一次(登录页/首屏在 initPrefs 前也吃到默认主题)。 */
ensureMedia()
apply()

/** 登录后调用: 拉服务端偏好。401(未登录)静默保持默认。 */
export async function initPrefs() {
  ensureMedia()
  try {
    const p = await fetchJSON('/api/auth/preferences')
    merge({
      theme: ['light', 'dark', 'system'].includes(p.theme) ? p.theme : 'light',
      wanUnit: p.wan_unit !== false,
      updownRed: p.updown_red !== false,
    })
  } catch { /* 未登录/离线: 保持默认 */ }
}

/** 用户登出时重置为默认, 防止上一个用户的偏好泄漏到登录页。 */
export function resetPrefs() {
  merge({ theme: 'light', wanUnit: true, updownRed: true })
}

/** 乐观保存: 界面立即生效, 持久化失败静默(下次进来会回读服务端值)。 */
export function setPrefs(partial) {
  merge(partial)
  fetchJSON('/api/auth/preferences', {
    method: 'PUT',
    body: JSON.stringify({
      ...(partial.theme !== undefined ? { theme: partial.theme } : {}),
      ...(partial.wanUnit !== undefined ? { wan_unit: partial.wanUnit } : {}),
      ...(partial.updownRed !== undefined ? { updown_red: partial.updownRed } : {}),
    }),
  }).catch(() => {})
}

/** 组件订阅: 任何偏好变化都触发重渲染(helpers.js 的纯函数在渲染期读单例)。 */
export function usePrefs() {
  return useSyncExternalStore(subscribePrefs, getPrefs, getPrefs)
}

/** 图表用: 仅当解析后主题/涨跌向变化时 bump(0,1,2…), useEffect 依赖它重绘 canvas。 */
let themeTick = 0
if (typeof window !== 'undefined') {
  window.addEventListener(THEME_EVENT, () => { themeTick += 1 })
}
export function useThemeTick() {
  return useSyncExternalStore(
    (cb) => {
      const h = () => cb()
      window.addEventListener(THEME_EVENT, h)
      return () => window.removeEventListener(THEME_EVENT, h)
    },
    () => themeTick,
    () => themeTick,
  )
}

/** 浅色卡预览等场景需要知道"当前解析主题"。 */
export function resolvedTheme() {
  return state.theme === 'system' ? (systemDark ? 'dark' : 'light') : state.theme
}
