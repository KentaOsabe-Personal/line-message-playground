import AuthGate from './AuthGate'
import DeliveryForm from './DeliveryForm'

export default function App() {
  return (
    <main>
      <h1>LINE Message Playground</h1>
      <p>LINE配信機能の検証環境</p>
      <AuthGate>
        <DeliveryForm />
      </AuthGate>
    </main>
  )
}
