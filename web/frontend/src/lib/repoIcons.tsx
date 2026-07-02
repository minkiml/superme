import {
  Rocket, Compass, Box, Brain, Cpu, Telescope, BarChart3, Target, Sprout, Wrench,
  FolderGit2, Sparkles, Atom, Globe, Zap, Flame, Leaf, BookOpen, Code2, Database,
  Layers, Orbit, Hexagon, Gem,
} from 'lucide-react'
import type { LucideIcon } from 'lucide-react'

// The curated repo-tag icon set — modern line icons (lucide) rather than emoji. A tag's icon is
// stored as a stable KEY (below); the orbit/inspector render it via <RepoIcon>. Adding one here
// makes it available everywhere at once.
export const REPO_ICONS: Record<string, LucideIcon> = {
  rocket: Rocket, compass: Compass, box: Box, brain: Brain, cpu: Cpu, telescope: Telescope,
  chart: BarChart3, target: Target, sprout: Sprout, wrench: Wrench, folder: FolderGit2,
  sparkles: Sparkles, atom: Atom, globe: Globe, zap: Zap, flame: Flame, leaf: Leaf,
  book: BookOpen, code: Code2, database: Database, layers: Layers, orbit: Orbit,
  hexagon: Hexagon, gem: Gem,
}

export const REPO_ICON_KEYS = Object.keys(REPO_ICONS)

// Render a stored icon key. Unknown/empty key → nothing (caller falls back to the color swatch).
export function RepoIcon({ name, size = 14, color, className }: { name?: string | null; size?: number; color?: string; className?: string }) {
  const Icon = name ? REPO_ICONS[name] : undefined
  if (!Icon) return null
  return <Icon size={size} className={className} style={color ? { color } : undefined} />
}
