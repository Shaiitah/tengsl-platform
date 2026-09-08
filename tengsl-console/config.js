// Dashboard talks to the backend through the same-origin Nginx proxy.
// This avoids CORS and keeps the authentication cookie same-origin.
window.TENGSL_CONFIG = {
  apiBase: "",
  wsBase: `${window.location.protocol === "https:" ? "wss:" : "ws:"}//${window.location.host}`,
};
