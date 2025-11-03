const STORAGE_KEY = "sado-trade-bot-api-base";
const AUTH_ERROR_STORAGE_KEY = "sado-trade-bot-auth-error";

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

const registerForm = document.getElementById("register-form");
const verifyForm = document.getElementById("verify-form");
const registerErrorEl = document.getElementById("register-error");
const registerHintEl = document.getElementById("register-hint");
const verifyErrorEl = document.getElementById("verify-error");
const verifyHintEl = document.getElementById("verify-hint");
const verifyEmailEl = document.getElementById("verify-email");
const registerVersionEl = document.getElementById("register-version");

const totpQrWrapper = document.getElementById("totp-qr-wrapper");
const totpQrImage = document.getElementById("totp-qr");
const totpSecretEl = document.getElementById("totp-secret");
const totpUriLink = document.getElementById("totp-uri");
const totpExtraEl = document.getElementById("totp-extra");
const copyTotpBtn = document.querySelector('[data-action="copy-totp"]');
const resendCodeBtn = document.querySelector('[data-action="resend-code"]');

const wizardSteps = document.querySelectorAll("[data-step]");

let apiBase = DEFAULT_API_BASE;
let currentEmail = "";

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

const requestApi = async (path, { method = "GET", body, headers = {} } = {}) => {
  const url = joinApiUrl(apiBase, path);
  const response = await fetch(url, {
    method,
    headers: { Accept: "application/json", "Content-Type": "application/json", ...headers },
    body,
  });

  const text = await response.text();
  let payload = {};
  if (text) {
    try {
      payload = JSON.parse(text);
    } catch (error) {
      throw new Error("서버 응답을 해석하지 못했습니다.");
    }
  }

  if (!response.ok) {
    const detail = payload?.detail || payload?.message || "요청이 실패했습니다.";
    throw new Error(detail);
  }

  return payload;
};

const setMessage = (el, message) => {
  if (!el) return;
  if (message) {
    el.textContent = message;
    el.hidden = false;
  } else {
    el.textContent = "";
    el.hidden = true;
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

const loadStoredApiBase = () => {
  try {
    const stored = window.localStorage?.getItem(STORAGE_KEY);
    return normaliseBase(stored) || DEFAULT_API_BASE;
  } catch (error) {
    console.warn("api base load failed", error);
    return DEFAULT_API_BASE;
  }
};

const clearAuthErrorFlag = () => {
  try {
    window.sessionStorage?.removeItem(AUTH_ERROR_STORAGE_KEY);
  } catch (error) {
    console.warn("auth error flag clear failed", error);
  }
};

const goToStep = (step) => {
  const targets = document.querySelectorAll('[data-step-target]');
  targets.forEach((target) => {
    target.hidden = target.dataset.stepTarget !== step;
  });
  wizardSteps.forEach((btn) => {
    btn.classList.toggle("auth-step--active", btn.dataset.step === step);
  });
};

const updateVersion = async () => {
  try {
    const response = await requestApi("/meta/version", { method: "GET" });
    const version = response?.version || response?.tag || "버전 정보를 불러오지 못했습니다.";
    if (registerVersionEl) {
      registerVersionEl.textContent = `버전 ${version}`;
    }
  } catch (error) {
    if (registerVersionEl) {
      registerVersionEl.textContent = "버전 정보를 불러오지 못했습니다.";
    }
  }
};

const handleRegister = async (event) => {
  event.preventDefault();
  setMessage(registerErrorEl, "");
  setMessage(registerHintEl, "");

  const emailInput = registerForm.elements["register-email"];
  const passwordInput = registerForm.elements["register-password"];
  const confirmInput = registerForm.elements["register-confirm"];

  const email = emailInput?.value?.trim() || "";
  const password = passwordInput?.value || "";
  const confirm = confirmInput?.value || "";

  if (password !== confirm) {
    setMessage(registerErrorEl, "비밀번호가 서로 일치하지 않습니다.");
    return;
  }

  try {
    const payload = await requestApi("/auth/register", {
      method: "POST",
      body: JSON.stringify({ email, password }),
    });
    currentEmail = email;
    persistApiBase(apiBase);
    setMessage(registerHintEl, payload?.message || "인증 코드가 전송되었습니다.");
    if (payload?.verification_code) {
      setMessage(registerHintEl, `${payload.message} (코드: ${payload.verification_code})`);
    }
    verifyEmailEl.textContent = `${email} 으로 발송된 인증 코드를 입력하세요.`;
    goToStep("verify");
  } catch (error) {
    setMessage(registerErrorEl, error.message);
  }
};

const handleVerify = async (event) => {
  event.preventDefault();
  if (!currentEmail) {
    setMessage(verifyErrorEl, "먼저 계정 정보를 등록하세요.");
    return;
  }
  setMessage(verifyErrorEl, "");
  setMessage(verifyHintEl, "");

  const codeField = verifyForm.elements["verify-code"];
  const code = codeField?.value?.trim() || "";
  if (code.length !== 6) {
    setMessage(verifyErrorEl, "6자리 인증 코드를 입력하세요.");
    return;
  }

  try {
    const payload = await requestApi("/auth/register/verify", {
      method: "POST",
      body: JSON.stringify({ email: currentEmail, code }),
    });
    setMessage(verifyHintEl, payload?.message || "이메일 인증이 완료되었습니다.");
    const secret = payload?.totp_secret || "";
    totpSecretEl.textContent = secret;
    totpSecretEl.classList.toggle("is-empty", !secret);
    if (payload?.totp_uri) {
      totpUriLink.href = payload.totp_uri;
      totpUriLink.hidden = false;
    } else {
      totpUriLink.hidden = true;
      totpUriLink.removeAttribute("href");
    }
    if (payload?.totp_qr) {
      totpQrImage.src = payload.totp_qr;
      totpQrImage.alt = "Google Authenticator 등록용 QR 코드";
      totpQrImage.hidden = false;
      totpQrWrapper.hidden = false;
    } else {
      totpQrWrapper.hidden = true;
      totpQrImage.removeAttribute("src");
      totpQrImage.hidden = true;
    }
    totpExtraEl.textContent = payload?.totp_required
      ? "Google Authenticator 등록 후 로그인 시 6자리 코드를 입력하세요."
      : "현재는 2차 인증이 선택 사항입니다.";
    if (!payload?.totp_qr && secret) {
      totpExtraEl.textContent += " QR 코드가 보이지 않으면 보안 키를 직접 입력하거나 아래 링크를 열어 등록하세요.";
    }
    goToStep("totp");
  } catch (error) {
    setMessage(verifyErrorEl, error.message);
  }
};

const handleResend = async () => {
  if (!currentEmail) {
    setMessage(verifyErrorEl, "먼저 계정 정보를 등록하세요.");
    return;
  }
  try {
    const payload = await requestApi("/auth/register/resend", { method: "POST" });
    setMessage(verifyHintEl, payload?.message || "새 인증 코드가 발송되었습니다.");
    if (payload?.verification_code) {
      setMessage(verifyHintEl, `${payload.message} (코드: ${payload.verification_code})`);
    }
  } catch (error) {
    setMessage(verifyErrorEl, error.message);
  }
};

const handleCopySecret = async () => {
  if (!totpSecretEl?.textContent) {
    return;
  }
  try {
    await navigator.clipboard.writeText(totpSecretEl.textContent);
    totpExtraEl.textContent = "보안 키를 복사했습니다. Google Authenticator에 붙여넣어 등록하세요.";
  } catch (error) {
    totpExtraEl.textContent = "보안 키를 복사하지 못했습니다. 직접 입력하세요.";
  }
};

const init = () => {
  apiBase = loadStoredApiBase();
  clearAuthErrorFlag();
  goToStep("register");
  updateVersion();

  registerForm?.addEventListener("submit", handleRegister);
  verifyForm?.addEventListener("submit", handleVerify);
  copyTotpBtn?.addEventListener("click", handleCopySecret);
  resendCodeBtn?.addEventListener("click", handleResend);

  wizardSteps.forEach((btn) => {
    btn.addEventListener("click", () => {
      const target = btn.dataset.step;
      if (target === "register") {
        goToStep("register");
      } else if (target === "verify" && currentEmail) {
        goToStep("verify");
      } else if (target === "totp" && totpSecretEl.textContent) {
        goToStep("totp");
      }
    });
  });
};

document.addEventListener("DOMContentLoaded", init);
