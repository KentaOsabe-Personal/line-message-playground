import AccountConsole from './AccountConsole'
import AuthGate from './AuthGate'
import DeliveryForm from './DeliveryForm'

export default function App() {
  return (
    <main>
      <h1>LINE Message Playground</h1>
      <p>LINE配信機能の検証環境</p>
      <AuthGate>{({
        session,
        getAccessToken,
        reauthenticate,
        reauthenticateForUnlink,
        unlinkReauthenticationReady,
        onSessionReceived,
        refreshSession,
      }) => (
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
          {session.state === 'authenticated' && <DeliveryForm />}
        </>
      )}</AuthGate>
    </main>
  )
}
