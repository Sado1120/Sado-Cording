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
const registerForm = document.getElementById("auth-register-form");
const verifyForm = document.getElementById("auth-verify-form");
const totpCard = document.getElementById("auth-totp-card");

const loginEmailInput = document.getElementById("auth-email");
const loginPasswordInput = document.getElementById("auth-password");
const loginTotpInput = document.getElementById("auth-totp");
const totpField = document.querySelector("[data-auth-totp-field]");
const totpLabel = document.getElementById("auth-totp-label");
const loginErrorEl = document.getElementById("auth-error");
const loginHintEl = document.getElementById("auth-footnote");

const registerEmailInput = document.getElementById("register-email");
const registerPasswordInput = document.getElementById("register-password");
const registerConfirmInput = document.getElementById("register-confirm");
const registerErrorEl = document.getElementById("auth-register-error");
const registerHintEl = document.getElementById("auth-register-hint");

const verifyCodeInput = document.getElementById("verify-code");
const verifyErrorEl = document.getElementById("auth-verify-error");
const verifyHintEl = document.getElementById("auth-verify-hint");
const verifyEmailEl = document.getElementById("auth-verify-email");

const totpInstructionsEl = document.getElementById("totp-instructions");
const totpQrWrapper = document.getElementById("totp-qr-wrapper");
const totpQrImage = document.getElementById("totp-qr");
const totpSecretEl = document.getElementById("totp-secret");
const totpExtraEl = document.getElementById("totp-extra");
const totpUriLink = document.getElementById("totp-uri");
const copyTotpBtn = document.querySelector('[data-action="copy-totp"]');
const goLoginBtn = document.querySelector('[data-action="go-login"]');

const versionEl = document.getElementById("auth-version");
const showRegisterBtn = document.querySelector('[data-action="show-register"]');
const showLoginButtons = document.querySelectorAll('[data-action="show-login"]');
const resendCodeBtn = document.querySelector('[data-action="resend-code"]');

const wizardSection = document.querySelector("[data-auth-wizard]");
const wizardSteps = document.querySelectorAll("[data-step]");

const STEP_FOR_VIEW = {
  register: "register",
  verify: "verify",
  totp: "totp",
};

let apiBase = DEFAULT_API_BASE;
let totpRequired = true;
let currentEmail = "";
let currentView = "login";
let currentTotpSecret = "";

const views = {
  login: loginForm,
  register: registerForm,
  verify: verifyForm,
  totp: totpCard,
};

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
const setRegisterError = (message) => setFieldMessage(registerErrorEl, message);
const setRegisterHint = (message) => setFieldMessage(registerHintEl, message);
const setVerifyError = (message) => setFieldMessage(verifyErrorEl, message);
const setVerifyHint = (message) => setFieldMessage(verifyHintEl, message);
const setTotpExtra = (message) => {
  if (!totpExtraEl) return;
  totpExtraEl.textContent = message || "";
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

const viewsList = ["login", "register", "verify", "totp"];

const updateWizardView = (view) => {
  if (!wizardSection) {
    return;
  }

  const stepKey = STEP_FOR_VIEW[view] || "register";
  wizardSection.classList.toggle("auth-wizard--active", view !== "login");

  wizardSteps.forEach((btn) => {
    const step = btn.dataset.step;
    if (!step) return;
    const isActive = step === stepKey;
    btn.classList.toggle("is-active", isActive);
    btn.setAttribute("aria-current", isActive ? "step" : "false");
    if (step === "verify") {
      btn.disabled = !currentEmail;
    } else if (step === "totp") {
      btn.disabled = !currentTotpSecret;
    } else {
      btn.disabled = false;
    }
  });
};

const focusForView = (view) => {
  if (view === "login") {
    if (loginEmailInput && !loginEmailInput.value.trim()) {
      loginEmailInput.focus();
    } else {
      loginPasswordInput?.focus();
    }
  } else if (view === "register") {
    registerEmailInput?.focus();
  } else if (view === "verify") {
    verifyCodeInput?.focus();
  } else if (view === "totp") {
    goLoginBtn?.focus();
  }
};

const setView = (view) => {
  if (!viewsList.includes(view)) {
    return;
  }
  currentView = view;
  updateWizardView(view);
  viewsList.forEach((name) => {
    const node = views[name];
    if (!node) return;
    if (name === view) {
      node.hidden = false;
      node.setAttribute("aria-hidden", "false");
    } else {
      node.hidden = true;
      node.setAttribute("aria-hidden", "true");
    }
  });
  if (view === "login") {
    setRegisterError("");
    setVerifyError("");
    setVerifyHint("");
  } else if (view === "register") {
    setLoginError("");
    setVerifyError("");
    setVerifyHint("");
  } else if (view === "verify") {
    setLoginError("");
  } else {
    setLoginError("");
    setRegisterError("");
    setVerifyError("");
  }
  window.requestAnimationFrame(() => focusForView(view));
};

const setTotpRequirement = (required) => {
  totpRequired = Boolean(required);
  if (loginTotpInput) {
    if (totpRequired) {
      loginTotpInput.setAttribute("required", "required");
    } else {
      loginTotpInput.removeAttribute("required");
      loginTotpInput.value = "";
    }
  }
  if (totpField) {
    totpField.classList.toggle("auth-card__field--optional", !totpRequired);
  }
  if (totpLabel) {
    totpLabel.textContent = totpRequired
      ? "Google Authenticator 코드 (필수)"
      : "Google Authenticator 코드 (선택)";
  }
  if (totpRequired) {
    setLoginHint("현재 설정에서는 Google Authenticator 보안 코드 입력이 필수입니다.");
  } else {
    setLoginHint("보안 코드가 설정되어 있지 않으면 로그인 시 생략할 수 있습니다.");
  }
};

const showTotpSetup = (payload) => {
  const secret = (payload?.totp_secret || "").trim();
  currentTotpSecret = secret;
  if (totpSecretEl) {
    totpSecretEl.textContent = secret || "보안 키를 불러오지 못했습니다.";
    totpSecretEl.classList.toggle("is-empty", !secret);
  }
  if (totpQrImage) {
    if (payload?.totp_qr) {
      totpQrImage.src = payload.totp_qr;
      totpQrImage.removeAttribute("hidden");
      totpQrImage.loading = "lazy";
      if (/^https?:/i.test(payload.totp_qr)) {
        totpQrImage.referrerPolicy = "no-referrer";
      } else {
        totpQrImage.removeAttribute("referrerpolicy");
      }
    } else {
      totpQrImage.setAttribute("hidden", "hidden");
      totpQrImage.removeAttribute("src");
    }
  }
  if (totpQrWrapper) {
    totpQrWrapper.hidden = !(payload?.totp_qr);
  }
  if (totpUriLink) {
    if (payload?.totp_uri) {
      totpUriLink.href = payload.totp_uri;
      totpUriLink.removeAttribute("hidden");
    } else {
      totpUriLink.setAttribute("hidden", "hidden");
      totpUriLink.removeAttribute("href");
    }
  }
  if (totpInstructionsEl) {
    totpInstructionsEl.textContent =
      "Google Authenticator 앱을 열고 QR 코드를 스캔하거나 보안 키를 입력하세요.";
  }
  const message = payload?.message || "Google Authenticator 등록 후 생성된 6자리 코드를 로그인에 사용하세요.";
  const isRemoteQr = Boolean(payload?.totp_qr && /^https?:/i.test(payload.totp_qr));
  if (isRemoteQr) {
    setTotpExtra(`${message} (인터넷 연결이 필요할 수 있습니다.)`);
  } else {
    setTotpExtra(message);
  }
  setTotpRequirement(payload?.totp_required ?? true);
  setLoginHint("Google Authenticator 보안 코드를 등록한 뒤 로그인하세요.");
  setView("totp");
};

const updateVerifyEmail = (email, extraMessage) => {
  if (!verifyEmailEl) return;
  const lines = [];
  if (email) {
    lines.push(`인증 대상 이메일: ${email}`);
  }
  if (extraMessage) {
    lines.push(extraMessage);
  }
  verifyEmailEl.textContent = lines.join(" \u00B7 ");
};

const fetchAuthStatus = async () => {
  try {
    const payload = await requestApi("/auth/status", { skipAuth: true });
    if (payload && typeof payload.totp_required === "boolean") {
      setTotpRequirement(payload.totp_required);
    } else {
      setTotpRequirement(true);
    }
    if (payload && typeof payload.email === "string") {
      currentEmail = payload.email;
      if (loginEmailInput && !loginEmailInput.value) {
        loginEmailInput.value = payload.email;
      }
    }
    updateWizardView(currentView);
    if (payload && payload.verification_pending && !payload.email_verified) {
      updateVerifyEmail(payload.email, "인증 코드를 입력하면 로그인을 진행할 수 있습니다.");
      setView("verify");
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

const restoreAuthErrorMessage = () => {
  try {
    const storage = window.sessionStorage;
    if (!storage) {
      return;
    }
    const message = storage.getItem(AUTH_ERROR_STORAGE_KEY);
    if (message) {
      setLoginError(message);
      storage.removeItem(AUTH_ERROR_STORAGE_KEY);
    }
  } catch (error) {
    console.warn("auth error restore failed", error);
  }
};

const performLogin = async (email, password, totpCode) => {
  const url = joinApiUrl(apiBase, "/auth/login");
  let response;
  try {
    response = await fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        email,
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
    const detail = payload?.detail || "이메일 또는 비밀번호를 확인해주세요.";
    throw new Error(detail);
  }

  if (!payload.access_token) {
    throw new Error("로그인 응답에 access_token이 없습니다.");
  }

  persistAuthToken(payload.access_token);
  return payload;
};

const handleLoginSubmit = async (event) => {
  event.preventDefault();
  if (!loginForm || !loginEmailInput || !loginPasswordInput) {
    return;
  }

  const email = loginEmailInput.value.trim();
  const password = loginPasswordInput.value;
  const totpCode = loginTotpInput ? loginTotpInput.value.trim() : "";

  if (!email || !password) {
    setLoginError("이메일과 비밀번호를 입력하세요.");
    return;
  }

  if (totpRequired && (!totpCode || totpCode.length < 6)) {
    setLoginError("Google Authenticator 보안 코드를 입력하세요.");
    return;
  }

  setLoginError("");
  const submitBtn = loginForm.querySelector('button[type="submit"]');
  submitBtn?.setAttribute("disabled", "disabled");

  try {
    await performLogin(email, password, totpCode);
    clearStoredAuthError();
    window.location.replace(DASHBOARD_URL);
  } catch (error) {
    const message = (error && error.message) || "로그인에 실패했습니다.";
    setLoginError(message);
    window.requestAnimationFrame(() => {
      loginPasswordInput?.focus();
      loginPasswordInput?.select?.();
      if (loginTotpInput && totpCode) {
        loginTotpInput.select?.();
      }
    });
  } finally {
    submitBtn?.removeAttribute("disabled");
  }
};

const handleRegisterSubmit = async (event) => {
  event.preventDefault();
  if (!registerForm || !registerEmailInput || !registerPasswordInput || !registerConfirmInput) {
    return;
  }

  const email = registerEmailInput.value.trim();
  const password = registerPasswordInput.value;
  const confirm = registerConfirmInput.value;

  if (!email || !password || !confirm) {
    setRegisterError("이메일과 비밀번호를 모두 입력하세요.");
    return;
  }

  if (password.length < 8) {
    setRegisterError("비밀번호는 8자 이상이어야 합니다.");
    return;
  }

  if (password !== confirm) {
    setRegisterError("비밀번호가 일치하지 않습니다.");
    return;
  }

  setRegisterError("");
  const submitBtn = registerForm.querySelector('button[type="submit"]');
  submitBtn?.setAttribute("disabled", "disabled");

  try {
    const payload = await requestApi("/auth/register", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ email, password }),
    });
    currentEmail = email;
    if (loginEmailInput) {
      loginEmailInput.value = email;
    }
    const expiresIn = payload?.expires_in ? ` (만료까지 약 ${Math.max(1, Math.round(payload.expires_in / 60))}분)` : "";
    const debugCode = payload?.verification_code ? ` 개발 환경 코드: ${payload.verification_code}` : "";
    setRegisterHint(`인증 코드가 전송되었습니다${expiresIn}.${debugCode}`.trim());
    setRegisterError("");
    updateVerifyEmail(email, debugCode ? `개발 모드 테스트 코드: ${payload.verification_code}` : "메일을 확인하세요.");
    if (verifyCodeInput) {
      verifyCodeInput.value = payload?.verification_code || "";
    }
    setVerifyHint("인증 코드를 입력하면 로그인을 진행할 수 있습니다.");
    setView("verify");
  } catch (error) {
    const message = (error && error.message) || "인증 코드 발급에 실패했습니다.";
    setRegisterError(message);
  } finally {
    submitBtn?.removeAttribute("disabled");
  }
};

const handleVerifySubmit = async (event) => {
  event.preventDefault();
  if (!verifyForm || !verifyCodeInput) {
    return;
  }

  const code = verifyCodeInput.value.trim();
  if (!code || code.length !== 6) {
    setVerifyError("6자리 인증 코드를 입력하세요.");
    return;
  }

  if (!currentEmail) {
    setVerifyError("등록된 이메일 정보를 찾을 수 없습니다. 다시 회원가입을 진행하세요.");
    return;
  }

  setVerifyError("");
  const submitBtn = verifyForm.querySelector('button[type="submit"]');
  submitBtn?.setAttribute("disabled", "disabled");

  try {
    const payload = await requestApi("/auth/register/verify", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ email: currentEmail, code }),
    });
    if (verifyCodeInput) {
      verifyCodeInput.value = "";
    }
    setVerifyHint("인증이 완료되었습니다. Google Authenticator 등록 단계로 이동합니다.");
    showTotpSetup(payload);
  } catch (error) {
    const message = (error && error.message) || "인증 코드 검증에 실패했습니다.";
    setVerifyError(message);
  } finally {
    submitBtn?.removeAttribute("disabled");
  }
};

const handleResendCode = async (event) => {
  event.preventDefault();
  if (!currentEmail) {
    setVerifyError("먼저 회원가입을 진행해주세요.");
    return;
  }

  try {
    const payload = await requestApi("/auth/register/resend", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
    });
    const debugCode = payload?.verification_code ? ` 개발 환경 코드: ${payload.verification_code}` : "";
    setVerifyHint(`새 인증 코드가 발송되었습니다.${debugCode}`.trim());
    if (verifyCodeInput && payload?.verification_code) {
      verifyCodeInput.value = payload.verification_code;
    }
  } catch (error) {
    const message = (error && error.message) || "코드 재전송에 실패했습니다.";
    setVerifyError(message);
  }
};

const handleCopyTotp = async (event) => {
  event.preventDefault();
  if (!currentTotpSecret) {
    setTotpExtra("복사할 보안 키가 없습니다. QR 코드를 다시 확인하세요.");
    return;
  }
  try {
    if (navigator?.clipboard?.writeText) {
      await navigator.clipboard.writeText(currentTotpSecret);
    } else {
      throw new Error("clipboard unavailable");
    }
    setTotpExtra("보안 키가 클립보드에 복사되었습니다.");
  } catch (error) {
    try {
      const temp = document.createElement("textarea");
      temp.value = currentTotpSecret;
      temp.setAttribute("readonly", "readonly");
      temp.style.position = "absolute";
      temp.style.left = "-9999px";
      document.body.appendChild(temp);
      temp.select();
      document.execCommand("copy");
      document.body.removeChild(temp);
      setTotpExtra("보안 키가 클립보드에 복사되었습니다.");
    } catch (copyError) {
      setTotpExtra("보안 키 복사에 실패했습니다. 수동으로 입력해주세요.");
    }
  }
};

const handleGoLogin = (event) => {
  event.preventDefault();
  setView("login");
  setLoginError("");
  setLoginHint("Google Authenticator에서 생성한 보안 코드를 입력한 뒤 로그인하세요.");
  if (loginPasswordInput) {
    loginPasswordInput.focus();
    loginPasswordInput.select?.();
  }
};

const bootstrapLogin = () => {
  apiBase = loadStoredApiBase();
  persistApiBase(apiBase);
  setTotpRequirement(true);
  setView("login");
  restoreAuthErrorMessage();
  fetchAuthStatus().catch(() => {});
  fetchVersion().catch(() => {});

  loginForm?.addEventListener("submit", handleLoginSubmit);
  registerForm?.addEventListener("submit", handleRegisterSubmit);
  verifyForm?.addEventListener("submit", handleVerifySubmit);
  if (showRegisterBtn) {
    showRegisterBtn.addEventListener("click", () => {
      setRegisterError("");
      setRegisterHint("");
      setView("register");
    });
  }
  showLoginButtons?.forEach((btn) => {
    btn.addEventListener("click", () => {
      setView("login");
    });
  });
  wizardSteps.forEach((btn) => {
    btn.addEventListener("click", (event) => {
      const step = event.currentTarget.dataset.step;
      if (!step) return;
      if (step === "register") {
        setView("register");
        return;
      }
      if (step === "verify") {
        if (!currentEmail) {
          setRegisterError("먼저 계정 정보를 입력해 인증 코드를 받아주세요.");
          setView("register");
          return;
        }
        setView("verify");
        return;
      }
      if (step === "totp") {
        if (!currentTotpSecret) {
          setTotpExtra("이메일 인증이 완료되면 보안 키가 생성됩니다.");
          return;
        }
        setView("totp");
      }
    });
  });
  if (resendCodeBtn) {
    resendCodeBtn.addEventListener("click", handleResendCode);
  }
  copyTotpBtn?.addEventListener("click", handleCopyTotp);
  goLoginBtn?.addEventListener("click", handleGoLogin);
};

bootstrapLogin();
