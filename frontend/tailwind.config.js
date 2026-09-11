/** @type {import('tailwindcss').Config} */
export default {
  darkMode: 'class',
  content: ['./index.html', './src/**/*.{ts,tsx}'],
  theme: {
    extend: {
      fontFamily: {
        // Inter for everything that is read, Space Grotesk for everything that is a mark:
        // the wordmark, section labels, the few places the interface should feel built
        // rather than written.
        sans: ['Inter', '-apple-system', 'BlinkMacSystemFont', 'Segoe UI', 'sans-serif'],
        display: ['Space Grotesk', 'Inter', '-apple-system', 'sans-serif'],
        mono: ['ui-monospace', 'SF Mono', 'SFMono-Regular', 'Menlo', 'monospace'],
      },
      colors: {
        // One accent, used only for what is interactive or alive. Everything else is zinc.
        // Anchored on #00d4aa. Note what the scale is *for*: 400-500 are surfaces that
        // carry dark text, 700-800 are the only shades dark enough to BE text on white.
        // A mint this luminous never carries white text — 2.6:1 — and pretending
        // otherwise is how an accent colour quietly becomes an accessibility bug.
        brand: {
          50: '#e6fff8', 100: '#c0fced', 200: '#84f7da', 300: '#46edc6',
          400: '#14dcb1', 500: '#00d4aa', 600: '#00b491', 700: '#008f74',
          800: '#00715d', 900: '#005c4c', 950: '#00352c',
        },
      },
      fontSize: {
        '2xs': ['0.6875rem', { lineHeight: '1rem' }],
      },
      keyframes: {
        'fade-up': { '0%': { opacity: '0', transform: 'translateY(6px)' },
                     '100%': { opacity: '1', transform: 'translateY(0)' } },
        'fade-in': { '0%': { opacity: '0' }, '100%': { opacity: '1' } },
        breathe: { '0%,100%': { opacity: '0.45', transform: 'scale(1)' },
                   '50%': { opacity: '0.85', transform: 'scale(1.06)' } },
        shimmer: { '0%': { backgroundPosition: '200% 0' }, '100%': { backgroundPosition: '-200% 0' } },
        'caret-blink': { '0%,70%,100%': { opacity: '1' }, '20%,50%': { opacity: '0' } },
      },
      animation: {
        'fade-up': 'fade-up .28s cubic-bezier(.2,.8,.2,1) both',
        'fade-in': 'fade-in .2s ease-out both',
        breathe: 'breathe 4.5s ease-in-out infinite',
        shimmer: 'shimmer 2.2s linear infinite',
        'caret-blink': 'caret-blink 1.1s steps(1) infinite',
      },
    },
  },
  plugins: [],
};
