/** @type {import('tailwindcss').Config} */
export default {
  darkMode: 'class',
  content: ['./index.html', './src/**/*.{ts,tsx}'],
  theme: {
    extend: {
      fontFamily: {
        sans: ['-apple-system', 'BlinkMacSystemFont', 'Inter', 'Segoe UI', 'Roboto', 'sans-serif'],
        mono: ['ui-monospace', 'SF Mono', 'SFMono-Regular', 'Menlo', 'monospace'],
      },
      colors: {
        // One accent, used only for what is interactive or alive. Everything else is zinc.
        brand: {
          50: '#f2f1ff', 100: '#e8e5ff', 200: '#d3ccff', 300: '#b5a9ff',
          400: '#9180ff', 500: '#6d5efc', 600: '#5b45f0', 700: '#4c36d1',
          800: '#3f2fa8', 900: '#362b85', 950: '#20174f',
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
