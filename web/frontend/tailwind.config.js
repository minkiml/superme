/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{ts,tsx}'],
  theme: {
    extend: {
      // Semantic, theme-aware tokens (values come from CSS vars in index.css, which
      // swap between light and dark). Components reference these — never raw slate/sky —
      // so the whole cockpit re-themes from one place.
      // Colors are `rgb(var(--c-x) / <alpha-value>)` so every `/opacity` modifier
      // resolves (e.g. `border-line/50`, `bg-accent/10`). The vars themselves are
      // space-separated RGB channels defined in index.css (light + .dark).
      colors: {
        app: 'rgb(var(--c-app) / <alpha-value>)', // page background
        sidebar: 'rgb(var(--c-sidebar) / <alpha-value>)', // the nav rail (deepest tier)
        surface: 'rgb(var(--c-surface) / <alpha-value>)', // panels, cards, rail
        sunken: 'rgb(var(--c-sunken) / <alpha-value>)', // inputs, code blocks
        hover: 'rgb(var(--c-hover) / <alpha-value>)', // subtle hover/active fills, pills
        line: 'rgb(var(--c-line) / <alpha-value>)', // borders, rings
        fg: 'rgb(var(--c-fg) / <alpha-value>)', // primary text
        muted: 'rgb(var(--c-muted) / <alpha-value>)', // secondary text
        faint: 'rgb(var(--c-faint) / <alpha-value>)', // hints / tertiary text
        accent: 'rgb(var(--c-accent) / <alpha-value>)', // accent fill (buttons, active)
        'accent-soft': 'rgb(var(--c-accent-soft) / <alpha-value>)', // accent subtle background
        'accent-text': 'rgb(var(--c-accent-text) / <alpha-value>)', // accent-colored text/icons
        'on-accent': 'rgb(var(--c-on-accent) / <alpha-value>)', // text on an accent fill
        success: 'rgb(var(--c-success) / <alpha-value>)',
        danger: 'rgb(var(--c-danger) / <alpha-value>)',
        warn: 'rgb(var(--c-warn) / <alpha-value>)',
        deputy: 'rgb(var(--c-deputy) / <alpha-value>)', // deputy-working attention tier (F1)
        // scope accents — dev/core/universal color-coding
        dev: 'rgb(var(--c-dev) / <alpha-value>)',
        core: 'rgb(var(--c-core) / <alpha-value>)',
        universal: 'rgb(var(--c-universal) / <alpha-value>)',
      },
      fontFamily: {
        sans: ['Inter Variable', 'Hanken Grotesk Variable', 'system-ui', '-apple-system', 'sans-serif'],
        mono: ['IBM Plex Mono', 'ui-monospace', 'monospace'],
      },
      backgroundImage: {
        iris: 'var(--grad-iris)', // the iridescent brand gradient
      },
    },
  },
  plugins: [],
}
