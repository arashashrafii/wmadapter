"""DeepSeek web selectors kept in one place for easier maintenance."""

CHAT_INPUTS = [
    "textarea#chat-input",
    'textarea[placeholder*="DeepSeek"]',
    "textarea.d96f2d2a",
]

FILE_INPUTS = [
    'input[type="file"]',
]

ATTACH_BUTTONS = [
    'button[aria-label*="Attach"]',
    'button[aria-label*="Upload"]',
    '[role="button"][aria-label*="Attach"]',
    '[role="button"][aria-label*="Upload"]',
]

SEND_BUTTONS = [
    '[role="button"].ds-button--primary:not(.ds-button--disabled)',
    'button[type="submit"]:not([disabled])',
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


COOKIE_ACCEPT = [
    'button:has-text("Accept")',
    'button:has-text("Accept all")',
    'button:has-text("I agree")',
    'button:has-text("Agree")',
    'div[role=button]:has-text("Accept")',
    'div[role=button]:has-text("Accept all")',
    '[data-testid*="accept"]',
    '[id*="accept"]',
    '[class*="accept"]',
]
