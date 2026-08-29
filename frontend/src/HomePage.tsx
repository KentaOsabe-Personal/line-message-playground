import { Link, useLocation } from 'react-router'

import PageFrame from './PageFrame'
import { meta } from './appRoutes'

const homeCards = [
  { to: '/liff/channels', label: 'チャネル管理' },
  { to: '/liff/account', label: 'アカウント管理' },
  { to: '/liff/rich-menus', label: 'リッチメニュー管理' },
  { to: '/liff/deliveries', label: 'メッセージ配信' },
] as const

export default function HomePage() {
  const location = useLocation()
  const pageMeta = meta(location.pathname)

  return (
    <PageFrame title={pageMeta.title} heading={pageMeta.heading} routeFocusKey={location.pathname}>
      <div className="home-card-grid">
        {homeCards.map((card) => (
          <Link key={card.to} className="home-card" data-home-card to={card.to}>{card.label}</Link>
        ))}
      </div>
    </PageFrame>
  )
}
