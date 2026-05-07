'use client'
import { createContext, useCallback, useContext, useEffect, useState, type ReactNode } from 'react'
import { translate, type Lang } from './dict'

const STORAGE_KEY = 'dracula-lang'

type I18nContextValue = {
  lang: Lang
  t: (text: string) => string
  toggle: () => void
  setLang: (lang: Lang) => void
}

const I18nContext = createContext<I18nContextValue | null>(null)

export function I18nProvider({ children }: { children: ReactNode }) {
  // SSR 默认 'zh'; mount 后读 localStorage
  const [lang, setLangState] = useState<Lang>('zh')

  useEffect(() => {
    try {
      const saved = localStorage.getItem(STORAGE_KEY)
      const next: Lang = saved === 'en' ? 'en' : 'zh'
      setLangState(next)
      document.documentElement.lang = next === 'en' ? 'en' : 'zh-CN'
    } catch {
      // 隐身模式 / 配额满 — 维持默认 zh
    }
  }, [])

  const setLang = (next: Lang) => {
    setLangState(next)
    document.documentElement.lang = next === 'en' ? 'en' : 'zh-CN'
    try {
      localStorage.setItem(STORAGE_KEY, next)
    } catch {
      // 静默忽略
    }
  }

  const toggle = () => setLang(lang === 'zh' ? 'en' : 'zh')

  const t = useCallback((text: string) => translate(text, lang), [lang])

  return <I18nContext.Provider value={{ lang, t, toggle, setLang }}>{children}</I18nContext.Provider>
}

export function useT() {
  const ctx = useContext(I18nContext)
  if (!ctx) throw new Error('useT must be used within I18nProvider')
  return ctx
}

/**
 * 防闪烁初始化: 在 layout.tsx <head> 同步设置 html lang, 避免 hydration mismatch.
 */
export const langInitScript = `(function(){try{var s=localStorage.getItem('${STORAGE_KEY}');var l=s==='en'?'en':'zh-CN';document.documentElement.setAttribute('lang',l);}catch(e){}})();`
