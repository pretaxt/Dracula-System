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
        bg:           'var(--color-bg)',
        surface:      'var(--color-surface)',
        'surface-elev': 'var(--color-surface-elev)',
        border:       'var(--color-border)',
        accent:       'var(--color-accent)',
        positive:     'var(--color-positive)',
        negative:     'var(--color-negative)',
      },
      fontFamily: {
        sans: ['Inter', 'system-ui', 'sans-serif'],
        mono: ['JetBrains Mono', 'Fira Code', 'monospace'],
      },
      width: { nav: 'var(--nav-width)' },
      height: { topbar: 'var(--topbar-height)' },
    },
  },
  plugins: [],
}
export default config
