import { useState } from 'react'
import TabBar from '@/ui/TabBar'
import { MemoryGovernance, PublishedInventory } from '@/features/config/LearningGovernance'
import { PaneHead } from '../controls'

// One repo's learning pipeline in its two readable states: the REVIEW queue, which only the owner
// can clear, and the PUBLISHED inventory.
//
// Universal artifacts belong to no project, so they live under System.

export default function ProjectLearning({ contextId, repoLabel }: { contextId: string; repoLabel: string }) {
  const [view, setView] = useState<'review' | 'published'>('review')
  return (
    <>
      <PaneHead
        title="Learning"
        lede="The two gates are yours. Nothing is published without you."
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
