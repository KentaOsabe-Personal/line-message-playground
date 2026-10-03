import { useMemo } from 'react'

import PageFrame from './PageFrame'
import TextJudgmentLab from './TextJudgmentLab'
import TextJudgmentLabAuthGate, {
  type LabAuthContext,
  type TextJudgmentLabAuthGateProps,
} from './TextJudgmentLabAuthGate'
import { createLabHttpClient, type LabHttpClient } from './textJudgmentLabApi'
import { useTextJudgmentLab } from './useTextJudgmentLab'

function LabConversation({
  context,
  api,
}: Readonly<{ context: LabAuthContext; api: LabHttpClient }>) {
  const [controller] = useTextJudgmentLab(api, context.invalidateAccess)
  return (
    <TextJudgmentLab
      controller={controller}
      access={context.access}
      getValidIdToken={context.getValidIdToken}
    />
  )
}

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
      description="通知相談の文章判定と会話分岐を確認します。"
    >
      <TextJudgmentLabAuthGate {...authGateProps} api={client}>
        {(context) => <LabConversation context={context} api={client} />}
      </TextJudgmentLabAuthGate>
    </PageFrame>
  )
}
