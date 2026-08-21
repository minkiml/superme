import { useCallback, useEffect, useState } from 'react'
import { getPublished, type PublishedItem } from '@/lib/api'

// Which universal artifacts the loop published, and their published record. Two surfaces need this
// and neither owns it.
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
