import AuthGuard from '@/components/shell/AuthGuard'
import SideNav from '@/components/shell/SideNav'
import TopBar from '@/components/shell/TopBar'
import Providers from '@/components/Providers'

export default function DashboardLayout({ children }: { children: React.ReactNode }) {
  return (
    <Providers>
      <AuthGuard>
        <div style={{ minHeight: '100vh', position: 'relative' }}>
          <SideNav />
          <div
            style={{
              marginLeft: 'var(--sidenav-width)',
              minHeight: '100vh',
              display: 'flex',
              flexDirection: 'column',
              position: 'relative',
              zIndex: 1,
            }}
          >
            <TopBar />
            <main style={{ flex: 1, padding: '24px', overflowX: 'hidden' }}>{children}</main>
          </div>
        </div>
      </AuthGuard>
    </Providers>
  )
}
