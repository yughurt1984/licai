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
 * data-theme 只落解析结果 light|dark|eyecare(不再有'跟随系统'); 字体三档(fontScale
 * s|m|l)写 html 根字号 inline style, 字号 rem 化后全站跟随。
 */

export const PREFS_EVENT = 'licai:prefs-changed'
export const THEME_EVENT = 'licai:theme-changed'

// 未登录默认 = 浅色(纯白) / ≥1万显万 / 红涨绿跌 / 中号字体
let state = { theme: 'light', wanUnit: true, updownRed: true, fontScale: 'm' }
const listeners = new Set()
let lastResolved = ''       // 上次 apply 的解析结果, 判断要不要发 THEME_EVENT

// 主题合法值; 旧存档的 'system'(跟随系统, 已下线)按浅色处理
const THEMES = ['light', 'dark', 'eyecare']
const resolveTheme = (theme) => (theme === 'dark' || theme === 'eyecare' ? theme : 'light')

// 字体三档(账户页·显示设置) → html 根字号。字号已全站迁 rem(text-[Npx]→rem),
// spacing 由 index.css 的 --spacing: 3.5px 钉死, 换档只有文字缩放、布局不动。
// s = 原设计基准(迁移前默认渲染), m = 默认档。
const FONT_SCALES = { s: '14px', m: '16px', l: '18px' }

function apply() {
  if (typeof document === 'undefined') return
  const el = document.documentElement
  const resolved = resolveTheme(state.theme)
  el.dataset.theme = resolved
  el.dataset.updown = state.updownRed ? 'red-up' : 'green-up'
  // 根字号: 三档都显式写 inline style(默认档也写 16px), 避免残留上一次的脏值
  el.style.fontSize = FONT_SCALES[state.fontScale] || FONT_SCALES.m
  const signature = `${resolved}|${state.updownRed ? 'r' : 'g'}`
  if (lastResolved && lastResolved !== signature) {
    window.dispatchEvent(new CustomEvent(THEME_EVENT))
  }
  lastResolved = signature
}

/** 模块加载即应用一次(登录页/首屏在 initPrefs 前也吃到默认主题)。 */
apply()

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
apply()

/** 登录后调用: 拉服务端偏好。401(未登录)静默保持默认。 */
export async function initPrefs() {
  try {
    const p = await fetchJSON('/api/auth/preferences')
    merge({
      theme: THEMES.includes(p.theme) ? p.theme : 'light',
      wanUnit: p.wan_unit !== false,
      updownRed: p.updown_red !== false,
      fontScale: ['s', 'm', 'l'].includes(p.font_scale) ? p.font_scale : 'm',
    })
  } catch { /* 未登录/离线: 保持默认 */ }
}

/** 用户登出时重置为默认, 防止上一个用户的偏好泄漏到登录页。 */
export function resetPrefs() {
  merge({ theme: 'light', wanUnit: true, updownRed: true, fontScale: 'm' })
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
      ...(partial.fontScale !== undefined ? { font_scale: partial.fontScale } : {}),
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

/** 浅色卡预览等场景需要知道"当前解析主题"(light|dark|eyecare)。 */
export function resolvedTheme() {
  return resolveTheme(state.theme)
}
