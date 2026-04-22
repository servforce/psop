(function () {
  const THEME_STORAGE_KEY = "psop-admin-scaffold-theme";

  function readStorage(key, fallback) {
    try {
      const value = window.localStorage.getItem(key);
      return value === null ? fallback : value;
    } catch {
      return fallback;
    }
  }

  function writeStorage(key, value) {
    try {
      window.localStorage.setItem(key, value);
    } catch {
      // Ignore storage failures and keep the shell usable.
    }
  }

  function applyTheme(theme) {
    document.body.classList.toggle("theme-dark", theme === "dark");
    document.documentElement.style.colorScheme = theme === "dark" ? "dark" : "light";
  }

  function createAppShell() {
    return {
      theme: "light",
      preservedAreas: [
        {
          kicker: "Backend",
          title: "FastAPI scaffold",
          summary: "App factory, config, logging, and health endpoints remain so the next runtime can be rebuilt cleanly."
        },
        {
          kicker: "Frontend",
          title: "Static admin shell",
          summary: "Alpine.js, TailwindCSS, local preview, and CSS build tooling stay in place without the old business UI."
        },
        {
          kicker: "Project",
          title: "Docs and scripts",
          summary: "Design docs, agent rules, skills, and dev scripts remain as the governing scaffold for the next implementation."
        }
      ],
      scaffoldItems: [
        "FastAPI app factory and generic system routes",
        "Shared settings, logging, and Python packaging metadata",
        "Static admin shell with Alpine.js and TailwindCSS",
        "Local dev scripts for backend, web, and test commands",
        "Documentation and project governance structure"
      ],
      nextSteps: [
        "Rebuild skills authoring flows in the Web IDE",
        "Introduce the skills-to-EG compile pipeline",
        "Implement runtime execution around the formal EG definition",
        "Restore replay and observability views on top of the new runtime"
      ],
      sourceDocs: [
        { label: "Overview Design v1", path: "docs/PSOP概要设计v1.md" },
        { label: "Detailed System Design v1", path: "docs/PSOP详细系统设计v1.md" },
        { label: "Execution Graph Formal v5", path: "docs/PSOP_execution_graph_formal_v5.md" }
      ],

      boot() {
        this.theme = readStorage(THEME_STORAGE_KEY, "light");
        applyTheme(this.theme);
      },

      toggleTheme() {
        this.theme = this.theme === "light" ? "dark" : "light";
        applyTheme(this.theme);
        writeStorage(THEME_STORAGE_KEY, this.theme);
      },

      get themeLabel() {
        return this.theme === "light" ? "Switch To Dark" : "Switch To Light";
      }
    };
  }

  document.addEventListener("alpine:init", function () {
    window.Alpine.data("appShell", createAppShell);
  });
})();
