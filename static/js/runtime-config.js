(function () {
  if (window.__PSOP_API_BASE_URL) {
    return;
  }

  if (window.location.port === "4173") {
    const apiHost = window.location.hostname === "0.0.0.0" ? "127.0.0.1" : window.location.hostname;
    window.__PSOP_API_BASE_URL = `${window.location.protocol}//${apiHost}:8001/api/v1`;
    return;
  }

  window.__PSOP_API_BASE_URL = "/api/v1";
})();
