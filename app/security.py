import bcrypt

MIN_PASSWORD_LENGTH = 8
# bcrypt only looks at the first 72 bytes; reject longer input instead of silently truncating.
MAX_PASSWORD_BYTES = 72


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode(), password_hash.encode())
    except ValueError:
        return False


# Checked when the email does not exist, so login timing does not reveal which emails are registered.
DUMMY_HASH = hash_password("timing-equalizer")


def password_problem(password: str) -> str | None:
    """Return a user-facing error message, or None if the password is acceptable."""
    if len(password) < MIN_PASSWORD_LENGTH:
        return f"Mật khẩu phải có ít nhất {MIN_PASSWORD_LENGTH} ký tự."
    if len(password.encode()) > MAX_PASSWORD_BYTES:
        return "Mật khẩu quá dài."
    return None


def normalize_email(email: str) -> str:
    return email.strip().lower()


def safe_next_url(next_url: str | None, default: str = "/") -> str:
    """Only allow same-site relative paths to prevent open redirects."""
    if next_url and next_url.startswith("/") and not next_url.startswith("//") and "\\" not in next_url:
        return next_url
    return default
