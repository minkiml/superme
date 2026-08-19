import { useCallback, useEffect, useState } from 'react'
import { getPublished, type PublishedItem } from '@/lib/api'

// Which universal artifacts the learning loop published, and which published record each one has.
//
// Two surfaces need this and neither owns it: Constitution badges a learned rule, and Skills/Agents
// open a learned file through the published-artifact governor rather than the plain file editor.
// Keyed `${form}:${slug}` — the same key both sides already build.
export function useUniversalPublished() {
  const [learned, setLearned] = useState<Set<string>>(new Set())
  const [byKey, setByKey] = useState<Map<string, PublishedItem>>(new Map())

  const reload = useCallback(() => {
    getPublished('global')
      .then((d) => {
        const present = d.published.filter((p) => p.present)
        setLearned(new Set(present.map((p) => `${p.form}:${p.slug}`)))
        setByKey(new Map(present.map((p) => [`${p.form}:${p.slug}`, p])))
      })
      .catch(() => {
        /* the badge is decoration; a failed read must not blank the inventory under it */
      })
  }, [])
  useEffect(() => { reload() }, [reload])

  return { learned, byKey, reload }
}
