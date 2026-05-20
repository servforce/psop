/** @type {import('tailwindcss').Config} */
module.exports = {
  content: [
    "./*.html",
    "./pages/**/*.html",
    "./js/**/*.js"
  ],
  theme: {
    extend: {
      colors: {
        slate: {
          950: "#09090b"
        }
      },
      boxShadow: {
        shell: "0 24px 60px rgba(9, 9, 11, 0.42)"
      }
    }
  }
};
