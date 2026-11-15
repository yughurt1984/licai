import { CrosshairMode } from 'lightweight-charts'
import { cssVar, withAlpha } from '../../colors'

/**
 * lightweight-charts 图表的主题敏感外观(文字/网格/十字线/轴边)。
 * 两个用途:
 *  1. 建图时铺初始选项(ProKline/MinuteChart);
 *  2. 主题切换时整组 applyOptions 重涂(图表颜色烘焙进 canvas, CSS 变量不会自己跟)。
 * 十字线用 accent 金半透明; 网格/轴边用文字色低透明度 —— 浅/深主题都成立。
 */
export const chartThemeOpts = () => ({
  layout: { textColor: cssVar('--color-text-dim', '#9aa0a6') },
  grid: { vertLines: { color: withAlpha(cssVar('--color-text'), 0.06) },
          horzLines: { color: withAlpha(cssVar('--color-text'), 0.06) } },
  crosshair: { mode: CrosshairMode.Normal,
    vertLine: { color: withAlpha(cssVar('--color-accent'), 0.5), width: 1, style: 2 },
    horzLine: { color: withAlpha(cssVar('--color-accent'), 0.5), width: 1, style: 2 } },
  rightPriceScale: { borderColor: withAlpha(cssVar('--color-text'), 0.12) },
  timeScale: { borderColor: withAlpha(cssVar('--color-text'), 0.12) },
})
