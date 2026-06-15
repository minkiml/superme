// Seed so the "/" palette isn't empty before the first turn populates the real list.
// Includes our shared command (/model) + the headless built-ins.
export const SEED_COMMANDS = ['model', 'compact', 'clear']

// 'claude-opus-4-7' -> 'opus 4.7'; falls back to the raw id.
export function formatModel(id: string | null): string {
  if (!id) return ''
  const m = id.match(/claude-([a-z]+)-(\d+)-(\d+)/)
  return m ? `${m[1]} ${m[2]}.${m[3]}` : id
}
