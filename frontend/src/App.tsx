import { useEffect, useState } from 'react'
import DeliveryForm from './DeliveryForm'

type Health = { status: string }

export default function App() {
  const [status, setStatus] = useState('確認中...')

  useEffect(() => {
    fetch('/api/health/')
      .then((response) => {
        if (!response.ok) throw new Error('API request failed')
        return response.json() as Promise<Health>
      })
      .then((data) => setStatus(data.status))
      .catch(() => setStatus('接続できません'))
  }, [])

  return (
    <main>
      <h1>LINE Message Playground</h1>
      <p>LINE配信機能の検証環境</p>
      <dl>
        <dt>Backend API</dt>
        <dd>{status}</dd>
      </dl>
      <DeliveryForm />
    </main>
  )
}
