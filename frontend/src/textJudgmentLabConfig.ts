const LAB_ENTRY_PATH = '/liff/labs/text-judgment'
const LAB_LIFF_PATH = '/labs/text-judgment'
const liffIdPattern = /^[A-Za-z0-9]+(?:-[A-Za-z0-9]+)*$/

export type TextJudgmentLabConfig = Readonly<{
  liffId: string
  liffUrl: `https://liff.line.me/${string}`
  entryUrl: `https://${string}/liff/labs/text-judgment`
}>

export class TextJudgmentLabConfigError extends Error {
  constructor() {
    super('TEXT_JUDGMENT_LAB_CONFIGURATION_INVALID')
    this.name = 'TextJudgmentLabConfigError'
  }
}

export function createTextJudgmentLabConfig(input: {
  liffId: string
  currentOrigin: string
}): TextJudgmentLabConfig {
  let origin: URL
  try {
    origin = new URL(input.currentOrigin)
  } catch {
    throw new TextJudgmentLabConfigError()
  }
  if (
    !liffIdPattern.test(input.liffId) ||
    origin.protocol !== 'https:' ||
    origin.origin !== input.currentOrigin ||
    origin.username !== '' ||
    origin.password !== '' ||
    origin.port !== '' ||
    origin.pathname !== '/' ||
    origin.search !== '' ||
    origin.hash !== ''
  )
    throw new TextJudgmentLabConfigError()

  return Object.freeze({
    liffId: input.liffId,
    liffUrl: `https://liff.line.me/${input.liffId}${LAB_LIFF_PATH}`,
    entryUrl: `${origin.origin}${LAB_ENTRY_PATH}` as `https://${string}/liff/labs/text-judgment`,
  })
}
