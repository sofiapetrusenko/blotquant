/** Tailwind v4 is a PostCSS plugin and needs no config file of its own: the design tokens live
 * in `app/globals.css` under `@theme`, which is where a reader looking for them will go. */
const config = {
  plugins: {
    '@tailwindcss/postcss': {},
  },
};

export default config;
