"""DeepSeek web selectors kept in one place for easier maintenance."""

CHAT_INPUTS = [
    "textarea#chat-input",
    'textarea[placeholder*="DeepSeek"]',
    "textarea.d96f2d2a",
]

RESPONSE_BLOCKS = [
    ".ds-markdown",
    "[class*='ds-markdown']",
]

LOGIN_EMAIL = 'input[placeholder="Phone number / email address"]'
LOGIN_PASSWORD = 'input[placeholder="Password"]'
LOGIN_AGREE = ".ds-checkbox"
LOGIN_SUBMIT = [
    '.ds-button--primary:has-text("Log in")',
    'div[role=button]:has-text("Log in")',
    ".ds-sign-up-form__register-button",
]
