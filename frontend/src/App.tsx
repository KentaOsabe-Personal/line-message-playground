import AccountConsole from './AccountConsole'
import AuthGate from './AuthGate'
import type { AuthGateContext } from './AuthGate'
import DeliveryForm from './DeliveryForm'

export function OwnerConsole({
  session,
  getAccessToken,
  reauthenticate,
  reauthenticateForUnlink,
  unlinkReauthenticationReady,
  onSessionReceived,
  refreshSession,
}: AuthGateContext) {
  return (
    <>
      <AccountConsole
        session={session}
        getAccessToken={getAccessToken}
        reauthenticate={reauthenticate}
        reauthenticateForUnlink={reauthenticateForUnlink}
        unlinkReauthenticationReady={unlinkReauthenticationReady}
        onSessionReceived={onSessionReceived}
        refreshSession={refreshSession}
      />
      {session.state === 'authenticated' && (
        <DeliveryForm onSessionInvalid={() => { void refreshSession() }} />
      )}
    </>
  )
}

export default function App() {
  return (
    <main>
      <h1>LINE Message Playground</h1>
      <p>LINE配信機能の検証環境</p>
      <AuthGate>{(context) => <OwnerConsole {...context} />}</AuthGate>
    </main>
  )
}
