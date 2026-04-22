/** @type {import('tailwindcss').Config} */
module.exports = {
  content: [
    "./*.html",
    "./pages/**/*.html",
    "./assets/js/**/*.js"
  ],
  theme: {
    extend: {
      colors: {
        slate: {
          950: "#020617"
        }
      },
      boxShadow: {
        shell: "0 24px 60px rgba(2, 6, 23, 0.42)"
      }
    }
  }
};
