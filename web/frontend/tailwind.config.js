/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{ts,tsx}'],
  theme: {
    extend: {
      // Semantic tokens, so the whole cockpit re-themes from index.css. Never raw slate/sky.
      // `rgb(var(--c-x) / <alpha-value>)` is what makes `bg-accent/10` resolve.
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
        deputy: 'rgb(var(--c-deputy) / <alpha-value>)', // deputy-working attention tier
        // scope accents — dev/core/universal color-coding
        dev: 'rgb(var(--c-dev) / <alpha-value>)',
        core: 'rgb(var(--c-core) / <alpha-value>)',
        universal: 'rgb(var(--c-universal) / <alpha-value>)',
        // work-kind accents — what an item IS (build vs research), its own axis
        'kind-build': 'rgb(var(--c-kind-build) / <alpha-value>)',
        'kind-research': 'rgb(var(--c-kind-research) / <alpha-value>)',
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
