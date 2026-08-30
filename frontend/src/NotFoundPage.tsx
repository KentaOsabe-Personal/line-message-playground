import { Link, useLocation } from 'react-router'

import PageFrame from './PageFrame'
import { meta } from './appRoutes'

export default function NotFoundPage() {
  const location = useLocation()
  const pageMeta = meta(location.pathname)

  return (
    <PageFrame title={pageMeta.title} heading={pageMeta.heading} routeFocusKey={location.pathname}>
      <Link className="not-found-link" to="/liff">トップへ戻る</Link>
    </PageFrame>
  )
}
