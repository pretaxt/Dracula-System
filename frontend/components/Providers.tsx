'use client'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { useState } from 'react'
import { ThemeProvider } from './theme/ThemeProvider'
import { I18nProvider } from './i18n/I18nProvider'

export default function Providers({ children }: { children: React.ReactNode }) {
  const [qc] = useState(() => new QueryClient({ defaultOptions: { queries: { staleTime: 15_000 } } }))
  return (
    <ThemeProvider>
      <I18nProvider>
        <QueryClientProvider client={qc}>{children}</QueryClientProvider>
      </I18nProvider>
    </ThemeProvider>
  )
}
