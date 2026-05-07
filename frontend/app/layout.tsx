import type { Metadata } from 'next'
import './globals.css'
import { themeInitScript } from '@/components/theme/ThemeProvider'
import { langInitScript } from '@/components/i18n/I18nProvider'

export const metadata: Metadata = {
  title: 'Dracula System',
  description: 'Multi-Strategy Crypto Arbitrage Terminal',
}

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="zh-CN" data-theme="dark" suppressHydrationWarning>
      <head>
        {/* 防闪烁: 在样式表加载前同步设置 data-theme + lang */}
        <script dangerouslySetInnerHTML={{ __html: themeInitScript }} />
        <script dangerouslySetInnerHTML={{ __html: langInitScript }} />
        <link rel="preconnect" href="https://fonts.googleapis.com" />
        <link rel="preconnect" href="https://fonts.gstatic.com" crossOrigin="anonymous" />
        <link
          href="https://fonts.googleapis.com/css2?family=Cinzel:wght@400;500;600;700&family=Cormorant+Garamond:wght@400;500;600;700&family=IBM+Plex+Mono:wght@400;500;600&family=IBM+Plex+Sans:wght@400;500;600&display=swap"
          rel="stylesheet"
        />
      </head>
      <body>{children}</body>
    </html>
  )
}
