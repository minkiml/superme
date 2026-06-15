/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{ts,tsx}'],
  theme: {
    extend: {
      // Semantic, theme-aware tokens (values come from CSS vars in index.css, which
      // swap between light and dark). Components reference these — never raw slate/sky —
      // so the whole cockpit re-themes from one place.
      colors: {
        app: 'var(--c-app)', // page background
        sidebar: 'var(--c-sidebar)', // the nav rail (deepest tier)
        surface: 'var(--c-surface)', // panels, cards, rail
        sunken: 'var(--c-sunken)', // inputs, code blocks
        hover: 'var(--c-hover)', // subtle hover/active fills, pills
        line: 'var(--c-line)', // borders, rings
        fg: 'var(--c-fg)', // primary text
        muted: 'var(--c-muted)', // secondary text
        faint: 'var(--c-faint)', // hints / tertiary text
        accent: 'var(--c-accent)', // accent fill (buttons, active)
        'accent-soft': 'var(--c-accent-soft)', // accent subtle background
        'accent-text': 'var(--c-accent-text)', // accent-colored text/icons
        'on-accent': 'var(--c-on-accent)', // text on an accent fill
        success: 'var(--c-success)',
        danger: 'var(--c-danger)',
        warn: 'var(--c-warn)',
      },
    },
  },
  plugins: [],
}
