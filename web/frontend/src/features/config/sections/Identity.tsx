import { useEffect, useState } from 'react'
import { FileText, X } from 'lucide-react'
import Markdown from '@/ui/Markdown'
import Modal from '@/ui/Modal'
import SourceEditor from '@/ui/SourceEditor'
import { useEditGate, EditActions } from '@/ui/EditGate'
import { getFoundation, saveFoundationFile, type FoundationFile } from '@/lib/api'
import { Loading, PaneHead } from '../controls'

// System artifacts › Identity & charters — the hand-authored system-prompt sources: who SuperMe is,
// and how it behaves in each mode. Editing is allowed and takes effect on the next turn.

const SCOPE_COLOR: Record<string, string> = {
  universal: 'text-universal',
  dev: 'text-dev',
  core: 'text-core',
}

/** The preview renders the BODY; edit mode keeps the raw file, frontmatter and all. */
function stripFrontmatter(text: string): string {
  const m = text.match(/^---\n[\s\S]*?\n---\n?/)
  return m ? text.slice(m[0].length) : text
}

// One-line preview: the first non-empty, non-heading line of the body.
function preview(body: string): string {
  const line = stripFrontmatter(body)
    .split('\n')
    .map((l) => l.trim())
    .find((l) => l && !l.startsWith('#') && !l.startsWith('---'))
  return line ?? ''
}

export default function Identity() {
  const [files, setFiles] = useState<FoundationFile[] | null>(null)
  const [err, setErr] = useState<string | null>(null)
  const [open, setOpen] = useState<FoundationFile | null>(null)

  function load() {
    getFoundation().then((d) => setFiles(d.files)).catch((e) => setErr(String(e)))
  }
  useEffect(load, [])

  return (
    <>
      <PaneHead
        title="Identity &amp; charters"
        scope="System artifacts"
        lede="Hand-authored system-prompt sources. An edit takes effect on the next turn."
      />
      {err ? (
        <div className="text-sm text-danger">Couldn’t load foundation files — {err}</div>
      ) : files === null ? (
        <Loading />
      ) : (
        <div className="grid cols-narrow gap-3">
          {files.map((f) => (
            <button
              key={f.key}
              onClick={() => f.present && setOpen(f)}
              disabled={!f.present}
              className="flex flex-col rounded-xl border border-line bg-surface px-4 py-3.5 text-left transition hover:border-faint disabled:cursor-not-allowed disabled:opacity-50"
            >
              <div className="flex items-center gap-2">
                <span className="text-[14px] font-semibold text-fg">{f.label}</span>
                <span className={`ml-auto text-[10px] font-medium uppercase tracking-wider ${SCOPE_COLOR[f.scope] ?? 'text-faint'}`}>
                  {f.scope}
                </span>
              </div>
              <p className={`mt-1.5 line-clamp-2 text-[12px] leading-relaxed ${f.present ? 'text-muted' : 'text-faint'}`}>
                {f.present ? preview(f.body) : 'not present'}
              </p>
            </button>
          ))}
        </div>
      )}
      {open && <FileViewer file={open} onClose={() => setOpen(null)} onSaved={() => { load(); setOpen(null) }} />}
    </>
  )
}

function FileViewer({ file, onClose, onSaved }: { file: FoundationFile; onClose: () => void; onSaved: () => void }) {
  const gate = useEditGate({
    saved: file.body,
    valid: (d) => !!d.trim(),
    commit: async (d) => { await saveFoundationFile(file.key, d); onSaved() },
  })
  const { editing, draft, err } = gate
  return (
    // While editing, an outside click never closes — only the X does, so unsaved input cannot be
    // discarded by a stray click on the scrim.
    <Modal onClose={onClose} column maxW={editing ? 'max-w-4xl' : 'max-w-3xl'} z="z-[60]" dismissable={!editing}>
      <div className="flex shrink-0 items-center gap-2 border-b border-line px-5 py-3.5">
        <FileText size={15} className={SCOPE_COLOR[file.scope] ?? 'text-muted'} />
        <span className="text-[15px] font-semibold text-fg">{file.label}</span>
        <span className={`text-[10px] font-medium uppercase tracking-wider ${SCOPE_COLOR[file.scope] ?? 'text-faint'}`}>{file.scope}</span>
        <div className="ml-auto flex items-center gap-1.5">
          <EditActions gate={gate} />
          <button onClick={onClose} className="rounded-md p-1 text-muted hover:bg-hover hover:text-fg">
            <X size={18} />
          </button>
        </div>
      </div>
      <div className="min-h-0 flex-1 overflow-y-auto px-5 py-4">
        {err && <div className="mb-2 text-sm text-danger">{err}</div>}
        {editing ? (
          <SourceEditor value={draft} onChange={gate.setDraft} surface="bg-sunken" />
        ) : (
          <Markdown text={stripFrontmatter(file.body)} variant="doc" tone={file.scope as 'universal' | 'dev' | 'core'} />
        )}
      </div>
    </Modal>
  )
}
