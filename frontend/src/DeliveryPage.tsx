import { useLocation } from 'react-router'

import DeliveryForm from './DeliveryForm'
import type { LinkedDeliveryApiClient } from './deliveryApi'
import PageFrame from './PageFrame'
import { meta } from './appRoutes'

export type DeliveryPageProps = Readonly<{
  linkedClient?: LinkedDeliveryApiClient
  createOperationId?: () => string
  onSessionInvalid?: () => void
}>

export default function DeliveryPage(props: DeliveryPageProps) {
  const location = useLocation()
  const pageMeta = meta(location.pathname)

  return (
    <PageFrame
      title={pageMeta.title}
      heading={pageMeta.heading}
      description="配信元と送信先を選び、内容を確認してからLINEへ送信します。"
      routeFocusKey={location.pathname}
    >
      <DeliveryForm {...props} />
    </PageFrame>
  )
}
