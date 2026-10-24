import pencil from './assets/rich-menu/pencil.svg'
import refreshDark from './assets/rich-menu/refresh-dark.svg'
import refreshLight from './assets/rich-menu/refresh-light.svg'
import checkDark from './assets/rich-menu/check-dark.svg'
import checkLight from './assets/rich-menu/check-light.svg'

export default function RichMenuDesignIcon({ kind }: { kind: 'edit' | 'refresh' | 'check' }) {
  if (kind === 'edit')
    return <img className="rich-menu-icon" src={pencil} width={16} height={16} alt="" />
  const size = kind === 'check' ? 14 : 16
  return (
    <span className="rich-menu-themed-icon" aria-hidden="true">
      <img
        className="icon-light"
        src={kind === 'check' ? checkLight : refreshLight}
        width={size}
        height={size}
        alt=""
      />
      <img
        className="icon-dark"
        src={kind === 'check' ? checkDark : refreshDark}
        width={size}
        height={size}
        alt=""
      />
    </span>
  )
}
