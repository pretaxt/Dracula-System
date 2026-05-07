'use client'
import { createContext, useContext, useEffect, useState, type ReactNode } from 'react'

export type Theme = 'dark' | 'light'

const STORAGE_KEY = 'dracula-theme'

type ThemeContextValue = {
  theme: Theme
  toggle: () => void
  setTheme: (theme: Theme) => void
}

const ThemeContext = createContext<ThemeContextValue | null>(null)

export function ThemeProvider({ children }: { children: ReactNode }) {
  // SSR 默认 dark; mount 后从 documentElement 读取真实值 (防闪烁脚本已设置)
  const [theme, setThemeState] = useState<Theme>('dark')

  useEffect(() => {
    const current = (document.documentElement.getAttribute('data-theme') as Theme) || 'dark'
    setThemeState(current)
  }, [])

  const setTheme = (next: Theme) => {
    setThemeState(next)
    document.documentElement.setAttribute('data-theme', next)
    try {
      localStorage.setItem(STORAGE_KEY, next)
    } catch {
      // localStorage 不可用 (隐身模式 / 配额满) — 静默忽略, 当前会话仍生效
    }
  }

  const toggle = () => setTheme(theme === 'dark' ? 'light' : 'dark')

  return <ThemeContext.Provider value={{ theme, toggle, setTheme }}>{children}</ThemeContext.Provider>
}

export function useTheme() {
  const ctx = useContext(ThemeContext)
  if (!ctx) throw new Error('useTheme must be used within ThemeProvider')
  return ctx
}

/**
 * 防闪烁初始化脚本 — 必须在 <html> 标签后立即同步执行,早于 React hydration。
 * 在 app/layout.tsx 的 <head> 中通过 <script dangerouslySetInnerHTML> 注入。
 */
export const themeInitScript = `(function(){try{var s=localStorage.getItem('${STORAGE_KEY}');var t=s==='light'?'light':'dark';document.documentElement.setAttribute('data-theme',t);}catch(e){document.documentElement.setAttribute('data-theme','dark');}})();`
