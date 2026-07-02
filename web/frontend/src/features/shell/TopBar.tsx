// Full-width top bar: just the brand. Theme lives in the nav footer now.
export default function TopBar() {
  return (
    <header className="flex h-14 shrink-0 items-center gap-4 border-b border-line bg-sidebar px-4">
      <div className="flex items-center gap-2.5">
        <span className="h-7 w-7 rounded-lg bg-iris shadow-sm" />
        <span className="text-[17px] font-semibold tracking-tight text-fg">superme</span>
      </div>
    </header>
  )
}
