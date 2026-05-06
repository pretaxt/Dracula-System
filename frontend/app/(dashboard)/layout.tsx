import AuthGuard from '@/components/shell/AuthGuard'
import TopNav from '@/components/shell/TopNav'
import Providers from '@/components/Providers'

export default function DashboardLayout({ children }: { children: React.ReactNode }) {
  return (
    <Providers>
      <AuthGuard>
        <div style={{
          minHeight: '100vh',
          background: 'var(--color-bg)',
          backgroundImage: 'linear-gradient(var(--grid-line) 1px, transparent 1px), linear-gradient(90deg, var(--grid-line) 1px, transparent 1px)',
          backgroundSize: '40px 40px',
        }}>
          <TopNav />
          <main style={{ maxWidth: 1800, margin: '0 auto', padding: '1.25rem 1.75rem' }}>
            {children}
          </main>
        </div>
      </AuthGuard>
    </Providers>
  )
}
