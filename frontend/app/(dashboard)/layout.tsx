import AuthGuard from '@/components/shell/AuthGuard'
import DashboardShell from '@/components/shell/DashboardShell'
import Providers from '@/components/Providers'

export default function DashboardLayout({ children }: { children: React.ReactNode }) {
  return (
    <Providers>
      <AuthGuard>
        <DashboardShell>{children}</DashboardShell>
      </AuthGuard>
    </Providers>
  )
}
