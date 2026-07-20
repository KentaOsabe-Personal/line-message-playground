const LIFF_ENTRY_PATH = '/liff'
const liffIdPattern = /^[A-Za-z0-9]+(?:-[A-Za-z0-9]+)*$/

export interface LiffRuntimeConfig {
  liffId: string
  liffUrl: `https://liff.line.me/${string}`
  endpointUrl: `https://${string}/liff`
  redirectUri: `https://${string}/liff`
}

export type LiffConfigInput = {
  liffId: string
  currentOrigin: string
  currentPathname: string
}

export class LiffConfigError extends Error {
  constructor() {
    super('LIFF_CONFIGURATION_INVALID')
    this.name = 'LiffConfigError'
  }
}

export function createLiffRuntimeConfig(input: LiffConfigInput): LiffRuntimeConfig {
  let origin: URL
  try {
    origin = new URL(input.currentOrigin)
  } catch {
    throw new LiffConfigError()
  }

  if (
    !liffIdPattern.test(input.liffId) ||
    input.currentPathname !== LIFF_ENTRY_PATH ||
    origin.protocol !== 'https:' ||
    origin.origin !== input.currentOrigin ||
    origin.username !== '' ||
    origin.password !== '' ||
    origin.port !== '' ||
    origin.pathname !== '/' ||
    origin.search !== '' ||
    origin.hash !== ''
  ) {
    throw new LiffConfigError()
  }

  const endpointUrl = `${origin.origin}${LIFF_ENTRY_PATH}` as `https://${string}/liff`
  return Object.freeze({
    liffId: input.liffId,
    liffUrl: `https://liff.line.me/${input.liffId}`,
    endpointUrl,
    redirectUri: endpointUrl,
  })
}
