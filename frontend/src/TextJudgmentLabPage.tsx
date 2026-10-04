import { useMemo } from 'react'

import PageFrame from './PageFrame'
import TextJudgmentLab from './TextJudgmentLab'
import TextJudgmentLabAuthGate, {
  type TextJudgmentLabAuthGateProps,
} from './TextJudgmentLabAuthGate'
import { createLabHttpClient, type LabHttpClient } from './textJudgmentLabApi'

export default function TextJudgmentLabPage({
  api,
  authGateProps,
}: Readonly<{
  api?: LabHttpClient
  authGateProps?: Omit<TextJudgmentLabAuthGateProps, 'children' | 'api'>
}>) {
  const client = useMemo(() => api ?? createLabHttpClient(), [api])
  return (
    <PageFrame
      title="文章判定ラボ"
      heading="文章判定ラボ"
      className="lab-page-frame"
      routeFocusKey="text-judgment-lab"
      description="自由に送って、Jevがどう読むか見てみよう。"
    >
      <TextJudgmentLabAuthGate {...authGateProps} api={client}>
        {(context) => <TextJudgmentLab context={context} api={client} />}
      </TextJudgmentLabAuthGate>
    </PageFrame>
  )
}
