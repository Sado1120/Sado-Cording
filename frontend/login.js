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

const loginForm = document.getElementById("auth-login-form");
const loginEmailInput = document.getElementById("auth-email");
const loginPasswordInput = document.getElementById("auth-password");
const loginTotpInput = document.getElementById("auth-totp");
const loginErrorEl = document.getElementById("auth-error");
const loginHintEl = document.getElementById("auth-footnote");
const totpLabel = document.getElementById("auth-totp-label");
const versionEl = document.getElementById("auth-version");

let apiBase = DEFAULT_API_BASE;
let totpRequired = true;

const normaliseBase = (value) => {
  if (!value || typeof value !== "string") {
    return "";
  }
  return value.trim().replace(/\/+$/, "");
};

const joinApiUrl = (base, path) => {
  const prefix = normaliseBase(base || DEFAULT_API_BASE) || DEFAULT_API_BASE;
  if (!path || path === "/") {
    return `${prefix}/`;
  }
  if (/^https?:\/\//i.test(path)) {
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
    return normaliseBase(stored) || DEFAULT_API_BASE;
  } catch (error) {
    console.warn("api base load failed", error);
    return DEFAULT_API_BASE;
  }
};

const persistApiBase = (value) => {
  const normalised = normaliseBase(value);
  if (!normalised) return;
  try {
    window.localStorage?.setItem(STORAGE_KEY, normalised);
  } catch (error) {
    console.warn("api base persist failed", error);
  }
};

const persistAuthToken = (token) => {
  try {
    window.localStorage?.setItem(AUTH_TOKEN_KEY, token);
  } catch (error) {
    console.warn("auth token persist failed", error);
  }
};

const clearAuthErrorFlag = () => {
  try {
    window.sessionStorage?.removeItem(AUTH_ERROR_STORAGE_KEY);
  } catch (error) {
    console.warn("auth error flag clear failed", error);
  }
};

const requestApi = async (path, { method = "GET", body, headers = {} } = {}) => {
  const url = joinApiUrl(apiBase, path);
  let response;
  try {
    response = await fetch(url, {
      method,
      headers: { Accept: "application/json", "Content-Type": "application/json", ...headers },
      body,
    });
  } catch (error) {
    throw new Error(`API 요청에 실패했습니다. ${error?.message ? `(${error.message})` : ''}`.trim());
  }

  const text = await response.text();
  let payload = {};
  if (text) {
    try {
      payload = JSON.parse(text);
    } catch (error) {
      if (!response.ok) {
        const preview = text.length > 180 ? `${text.slice(0, 177)}…` : text;
        throw new Error(`서버 응답을 해석하지 못했습니다: ${preview}`);
      }
      return { ok: true, raw: text };
    }
  }

  if (!response.ok) {
    const detail =
      payload?.detail ||
      payload?.message ||
      (response.status ? `요청이 실패했습니다. (HTTP ${response.status})` : "요청이 실패했습니다.");
    throw new Error(detail);
  }

  return payload;
};

const setFieldMessage = (element, message) => {
  if (!element) return;
  if (message) {
    element.textContent = message;
    element.hidden = false;
  } else {
    element.textContent = "";
    element.hidden = true;
  }
};

const setLoginError = (message) => setFieldMessage(loginErrorEl, message);

const setLoginHint = (message) => {
  if (!loginHintEl) return;
  loginHintEl.textContent = message || "";
};

const fetchVersion = async () => {
  try {
    const payload = await requestApi("/meta/version", { method: "GET" });
    const version = payload?.version || payload?.tag || "알 수 없음";
    if (versionEl) {
      versionEl.textContent = `버전 ${version}`;
    }
  } catch (error) {
    if (versionEl) {
      versionEl.textContent = "버전 정보를 불러오지 못했습니다.";
    }
  }
};

const fetchAuthStatus = async () => {
  try {
    const payload = await requestApi("/auth/status", { method: "GET" });
    totpRequired = Boolean(payload?.totp_required);
    if (totpRequired) {
      totpLabel.textContent = "Google Authenticator 코드 (일반 계정 필수)";
      setLoginHint(
        "일반 계정은 Google Authenticator 앱에서 6자리 코드를 입력해야 합니다. 관리자 계정은 생략할 수 있습니다."
      );
    } else {
      totpLabel.textContent = "Google Authenticator 코드 (선택)";
      setLoginHint("2단계 인증을 활성화하면 더욱 안전하게 이용할 수 있습니다.");
    }
    if (loginTotpInput) {
      loginTotpInput.setAttribute("aria-required", totpRequired ? "true" : "false");
    }
  } catch (error) {
    console.warn("auth status load failed", error);
    totpRequired = true;
    if (totpLabel) {
      totpLabel.textContent = "Google Authenticator 코드 (일반 계정 필수)";
    }
    setLoginHint("서버 상태를 확인하지 못했습니다. 기본적으로 일반 계정은 2단계 인증이 필요합니다.");
    if (loginTotpInput) {
      loginTotpInput.setAttribute("aria-required", "true");
    }
  }
};

const performLogin = async (email, password, totpCode) => {
  const payload = await requestApi("/auth/login", {
    method: "POST",
    body: JSON.stringify({
      email,
      password,
      ...(totpCode ? { totp_code: totpCode } : {}),
    }),
  });

  if (!payload?.access_token) {
    throw new Error("로그인 응답에 access_token이 없습니다.");
  }

  persistAuthToken(payload.access_token);
  return payload;
};

const handleLoginSubmit = async (event) => {
  event.preventDefault();

  if (!loginEmailInput || !loginPasswordInput) {
    return;
  }

  const email = loginEmailInput.value.trim();
  const password = loginPasswordInput.value;
  const totpCode = loginTotpInput?.value?.trim() || "";

  if (!email || !password) {
    setLoginError("이메일과 비밀번호를 모두 입력하세요.");
    return;
  }

  setLoginError("");

  const submitBtn = loginForm?.querySelector('button[type="submit"]');
  submitBtn?.setAttribute("disabled", "disabled");

  try {
    await performLogin(email, password, totpCode);
    persistApiBase(apiBase);
    clearAuthErrorFlag();
    window.location.replace(DASHBOARD_URL);
  } catch (error) {
    const message = error?.message || "로그인에 실패했습니다.";
    setLoginError(message);
    window.sessionStorage?.setItem(AUTH_ERROR_STORAGE_KEY, message);
    loginPasswordInput.focus();
    loginPasswordInput.select?.();
  } finally {
    submitBtn?.removeAttribute("disabled");
  }
};

const init = () => {
  apiBase = loadStoredApiBase();
  clearAuthErrorFlag();
  fetchVersion();
  fetchAuthStatus();
  loginForm?.addEventListener("submit", handleLoginSubmit);
};

document.addEventListener("DOMContentLoaded", init);
