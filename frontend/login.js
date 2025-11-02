const STORAGE_KEY = "sado-trade-bot-api-base";
const AUTH_TOKEN_KEY = "sado-trade-bot-auth-token";
const AUTH_ERROR_STORAGE_KEY = "sado-trade-bot-auth-error";
const DASHBOARD_URL = "index.html";

const DEFAULT_API_BASE = (() => {
  if (window.__SADO_API_BASE__) {
    return window.__SADO_API_BASE__;
  }
  const { origin } = window.location;
  if (origin.includes(":8501")) {
    return `${origin}/api`;
  }
  if (origin.startsWith("http://127.0.0.1") || origin.startsWith("http://localhost")) {
    return "http://127.0.0.1:8000";
  }
  return `${origin}/api`;
})();

const loginForm = document.getElementById("auth-form");
const usernameInput = document.getElementById("auth-username");
const passwordInput = document.getElementById("auth-password");
const totpInput = document.getElementById("auth-totp");
const totpField = document.querySelector("[data-auth-totp-field]");
const totpLabel = document.getElementById("auth-totp-label");
const errorEl = document.getElementById("auth-error");
const hintEl = document.getElementById("auth-footnote");
const submitBtn = loginForm?.querySelector('button[type="submit"]');
const versionEl = document.getElementById("auth-version");

let apiBase = DEFAULT_API_BASE;
let totpRequired = true;

const normaliseBase = (value) => {
  if (value === undefined || value === null) {
    return "";
  }
  if (typeof value !== "string") {
    return "";
  }
  const trimmed = value.trim();
  if (!trimmed) {
    return "";
  }
  return trimmed.replace(/\/+$/, "");
};

const joinApiUrl = (base, path) => {
  const prefix = normaliseBase(base || DEFAULT_API_BASE) || DEFAULT_API_BASE;
  if (!path || path === "/") {
    return `${prefix}/`;
  }
  if (path.startsWith("http://") || path.startsWith("https://")) {
    return path;
  }
  if (path.startsWith("/")) {
    return `${prefix}${path}`;
  }
  return `${prefix}/${path}`;
};

const loadStoredApiBase = () => {
  try {
    const stored = window.localStorage?.getItem(STORAGE_KEY);
    if (!stored) {
      return DEFAULT_API_BASE;
    }
    return normaliseBase(stored) || DEFAULT_API_BASE;
  } catch (error) {
    console.warn("api base load failed", error);
    return DEFAULT_API_BASE;
  }
};

const persistApiBase = (value) => {
  const normalised = normaliseBase(value);
  if (!normalised) {
    return;
  }
  try {
    window.localStorage?.setItem(STORAGE_KEY, normalised);
  } catch (error) {
    console.warn("api base persist failed", error);
  }
};

const setError = (message) => {
  if (!errorEl) return;
  if (message) {
    errorEl.textContent = message;
    errorEl.hidden = false;
  } else {
    errorEl.textContent = "";
    errorEl.hidden = true;
  }
};

const setTotpRequirement = (required) => {
  totpRequired = Boolean(required);
  if (totpInput) {
    if (totpRequired) {
      totpInput.setAttribute("required", "required");
    } else {
      totpInput.removeAttribute("required");
    }
  }
  if (totpField) {
    totpField.classList.toggle("auth-card__field--optional", !totpRequired);
  }
  if (totpLabel) {
    totpLabel.textContent = totpRequired
      ? "Google Authenticator 코드"
      : "Google Authenticator 코드 (선택)";
  }
  if (hintEl) {
    hintEl.textContent = totpRequired
      ? "현재 설정에서는 Google Authenticator 보안 코드 입력이 필수입니다."
      : "현재 설정에서는 Google Authenticator 코드를 입력하지 않아도 됩니다.";
  }
};

const persistAuthToken = (token) => {
  try {
    window.localStorage?.setItem(AUTH_TOKEN_KEY, token);
  } catch (error) {
    console.warn("auth token persist failed", error);
  }
};

const clearStoredAuthError = () => {
  try {
    window.sessionStorage?.removeItem(AUTH_ERROR_STORAGE_KEY);
  } catch (error) {
    console.warn("auth error clear failed", error);
  }
};

const requestApi = async (path, { method = "GET", headers = {}, body = undefined, skipAuth = true } = {}) => {
  const url = joinApiUrl(apiBase, path);
  const requestHeaders = { Accept: "application/json", ...headers };
  if (!skipAuth) {
    try {
      const token = window.localStorage?.getItem(AUTH_TOKEN_KEY);
      if (token) {
        requestHeaders.Authorization = `Bearer ${token}`;
      }
    } catch (error) {
      console.warn("auth token load failed", error);
    }
  }

  const response = await fetch(url, { method, headers: requestHeaders, body });
  if (response.status === 204) {
    return {};
  }
  let payload = {};
  const text = await response.text();
  if (text) {
    try {
      payload = JSON.parse(text);
    } catch (error) {
      throw new Error("API 응답을 해석하지 못했습니다.");
    }
  }
  if (!response.ok) {
    const detail = payload?.detail || "요청이 실패했습니다.";
    throw new Error(detail);
  }
  return payload;
};

const fetchAuthStatus = async () => {
  try {
    const payload = await requestApi("/auth/status", { skipAuth: true });
    if (payload && typeof payload.totp_required === "boolean") {
      setTotpRequirement(payload.totp_required);
    } else {
      setTotpRequirement(true);
    }
  } catch (error) {
    setTotpRequirement(true);
  }
};

const fetchVersion = async () => {
  if (!versionEl) return;
  try {
    const payload = await requestApi("/meta/version", { skipAuth: true });
    if (payload && payload.version) {
      versionEl.textContent = `버전 ${payload.version}`;
    } else {
      versionEl.textContent = "버전 정보를 확인할 수 없습니다.";
    }
  } catch (error) {
    versionEl.textContent = "버전 정보를 확인할 수 없습니다.";
  }
};

const performLogin = async (username, password, totpCode) => {
  const url = joinApiUrl(apiBase, "/auth/login");
  let response;
  try {
    response = await fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        username,
        password,
        ...(totpCode ? { totp_code: totpCode } : {}),
      }),
    });
  } catch (error) {
    throw new Error("로그인 요청에 실패했습니다. 네트워크를 확인하세요.");
  }

  let payload = {};
  try {
    payload = await response.json();
  } catch (error) {
    payload = {};
  }

  if (!response.ok) {
    const detail = payload?.detail || "아이디 또는 비밀번호를 확인해주세요.";
    throw new Error(detail);
  }

  if (!payload.access_token) {
    throw new Error("로그인 응답에 access_token이 없습니다.");
  }

  persistAuthToken(payload.access_token);
  return payload;
};

const focusInitialField = () => {
  if (usernameInput && !usernameInput.value.trim()) {
    usernameInput.focus();
    return;
  }
  passwordInput?.focus();
};

const restoreAuthErrorMessage = () => {
  try {
    const storage = window.sessionStorage;
    if (!storage) {
      return;
    }
    const message = storage.getItem(AUTH_ERROR_STORAGE_KEY);
    if (message) {
      setError(message);
      storage.removeItem(AUTH_ERROR_STORAGE_KEY);
    }
  } catch (error) {
    console.warn("auth error restore failed", error);
  }
};

const handleSubmit = async (event) => {
  event.preventDefault();
  if (!loginForm || !usernameInput || !passwordInput) {
    return;
  }

  const username = usernameInput.value.trim();
  const password = passwordInput.value;
  const totpCode = totpInput ? totpInput.value.trim() : "";

  if (!username || !password) {
    setError("아이디와 비밀번호를 입력하세요.");
    return;
  }

  if (totpRequired && (!totpCode || totpCode.length < 6)) {
    setError("Google Authenticator 보안 코드를 입력하세요.");
    return;
  }

  setError("");
  submitBtn?.setAttribute("disabled", "disabled");

  try {
    await performLogin(username, password, totpCode);
    clearStoredAuthError();
    window.location.replace(DASHBOARD_URL);
  } catch (error) {
    const message = (error && error.message) || "로그인에 실패했습니다.";
    setError(message);
    window.requestAnimationFrame(() => {
      passwordInput?.focus();
      passwordInput?.select?.();
      if (totpInput && totpCode) {
        totpInput.select?.();
      }
    });
  } finally {
    submitBtn?.removeAttribute("disabled");
  }
};

const bootstrapLogin = () => {
  apiBase = loadStoredApiBase();
  persistApiBase(apiBase);
  setTotpRequirement(true);
  restoreAuthErrorMessage();
  fetchAuthStatus().catch(() => {});
  fetchVersion().catch(() => {});
  focusInitialField();
  loginForm?.addEventListener("submit", handleSubmit);
};

bootstrapLogin();
