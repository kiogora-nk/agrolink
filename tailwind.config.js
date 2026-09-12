/** @type {import('tailwindcss').Config} */
// Used by the standalone Tailwind CLI to precompile the utility classes the
// templates use, so pages ship real CSS instead of downloading and running
// the ~110KB Play CDN script on every (mostly mobile) page load.
module.exports = {
  content: ['./templates/**/*.html', './static/js/*.js'],
  theme: {
    extend: {
      colors: {
        // Brand palette already used across templates via Tailwind's
        // default amber/slate scales - nothing custom needed yet.
      },
    },
  },
  corePlugins: {
    // Templates rarely use these; disabling shrinks the output.
    preflight: true,
  },
};
