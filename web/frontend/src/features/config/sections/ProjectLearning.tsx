import { useState } from 'react'
import TabBar from '@/ui/TabBar'
import { MemoryGovernance, PublishedInventory } from '@/features/config/LearningGovernance'
import { PaneHead } from '../controls'

// Project › Learning — one repo's learning pipeline, in the two states it can be read in: the
// REVIEW queue (candidates and proposals waiting at one of the two gates, which is work only the
// owner can do) and the PUBLISHED inventory (what the loop has already landed in the live harness).
//
// Universal skills and agents are not here: they belong to no project, so they live under System
// artifacts. What this shows is what THIS repo learned.

export default function ProjectLearning({ contextId, repoLabel }: { contextId: string; repoLabel: string }) {
  const [view, setView] = useState<'review' | 'published'>('review')
  return (
    <>
      <PaneHead
        title="Learning"
        scope={repoLabel}
        lede="Captured → distilled → forged → published. The two gates are yours; nothing is published without you."
      />
      <TabBar
        className="mb-5"
        variant="outlined"
        full
        value={view}
        onChange={setView}
        tabs={[['review', 'Review'], ['published', 'Published']] as const}
      />
      {view === 'review' ? <MemoryGovernance contextId={contextId} /> : <PublishedInventory contextId={contextId} />}
    </>
  )
}
