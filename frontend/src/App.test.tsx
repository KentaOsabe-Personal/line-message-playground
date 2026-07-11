import { renderToString } from 'react-dom/server'
import { expect, test } from 'vitest'
import App from './App'

test('renders the application title', () => {
  expect(renderToString(<App />)).toContain('LINE Message Playground')
})
