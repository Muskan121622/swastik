/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{ts,tsx}'],
  theme: {
    extend: {
      colors: {
        ink: '#e6edf7',
        calm: '#38bdf8',
        sea: '#8b5cf6',
        warn: '#fbbf24',
        danger: '#fb7185',
        paper: '#0b1220',
      },
      boxShadow: {
        glass: '0 8px 32px rgba(2, 6, 23, 0.55)',
        glow: '0 4px 24px rgba(56, 189, 248, 0.30)',
      },
      fontFamily: {
        sans: ['Inter', 'system-ui', 'Segoe UI', 'sans-serif'],
      },
    },
  },
  plugins: [],
}
