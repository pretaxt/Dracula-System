import type { Config } from 'tailwindcss'

const config: Config = {
  content: [
    './pages/**/*.{js,ts,jsx,tsx,mdx}',
    './components/**/*.{js,ts,jsx,tsx,mdx}',
    './app/**/*.{js,ts,jsx,tsx,mdx}',
  ],
  theme: {
    extend: {
      colors: {
        // 背景层级
        'bg-deepest':    'var(--bg-deepest)',
        'bg-base':       'var(--bg-base)',
        'bg-card':       'var(--bg-card)',
        'bg-card-hover': 'var(--bg-card-hover)',
        'bg-elevated':   'var(--bg-elevated)',
        // 边框
        'border-subtle':  'var(--border-subtle)',
        'border-default': 'var(--border-default)',
        'border-strong':  'var(--border-strong)',
        // 文字
        'text-primary':   'var(--text-primary)',
        'text-secondary': 'var(--text-secondary)',
        'text-tertiary':  'var(--text-tertiary)',
        'text-muted':     'var(--text-muted)',
        // 强调色
        'blood':        'var(--accent-blood)',
        'blood-dim':    'var(--accent-blood-dim)',
        'blood-bright': 'var(--accent-blood-bright)',
        'silver':       'var(--accent-silver)',
        'gold':         'var(--accent-gold)',
        'emerald':      'var(--accent-emerald)',
        'azure':        'var(--accent-azure)',
        // 状态
        'status-active':   'var(--status-active)',
        'status-warn':     'var(--status-warn)',
        'status-critical': 'var(--status-critical)',
        'status-paused':   'var(--status-paused)',
      },
      fontFamily: {
        display: ['Cinzel', 'Cormorant Garamond', 'serif'],
        serif:   ['Cormorant Garamond', 'Georgia', 'serif'],
        sans:    ['IBM Plex Sans', 'system-ui', 'sans-serif'],
        mono:    ['IBM Plex Mono', 'JetBrains Mono', 'monospace'],
      },
      width:  { sidenav: 'var(--sidenav-width)' },
      height: { topbar: 'var(--topbar-height)' },
      borderRadius: {
        xs: 'var(--radius-xs)',
        sm: 'var(--radius-sm)',
        md: 'var(--radius-md)',
        lg: 'var(--radius-lg)',
      },
    },
  },
  plugins: [],
}
export default config
