import AuthGuard from '@/components/shell/AuthGuard'
import SideNav from '@/components/shell/SideNav'
import Providers from '@/components/Providers'

export default function DashboardLayout({ children }: { children: React.ReactNode }) {
  return (
    <Providers>
      <AuthGuard>
        <div style={{ display: 'flex' }}>
          <SideNav />
          <main style={{ marginLeft: 'var(--nav-width)', flex: 1, minHeight: '100vh', padding: '2rem', background: 'var(--color-bg)' }}>
            {children}
          </main>
        </div>
      </AuthGuard>
    </Providers>
  )
}
