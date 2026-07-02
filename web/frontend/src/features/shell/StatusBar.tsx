// Slim full-width status bar (bottom of the System & Dev area). Will surface live
// run/activity signal once wired; placeholder for now (no fake alerts).
export default function StatusBar() {
  return (
    <div className="flex h-8 shrink-0 items-center gap-3 border-t border-line bg-sidebar px-4 text-[13px] text-faint">
      <span className="font-semibold uppercase tracking-wider">Activity</span>
      <span className="flex items-center gap-1.5">
        <span className="h-1.5 w-1.5 rounded-full bg-faint" />
        live run &amp; activity signal appears here
      </span>
    </div>
  )
}
