from app.models import UserRole


def register(client, email="hs@example.com", password="password123", confirm=None, name="Nguyễn Văn A"):
    return client.post(
        "/register",
        data={"full_name": name, "email": email, "password": password, "password_confirm": confirm or password},
        follow_redirects=False,
    )


def login(client, email, password="password123", next_url=""):
    return client.post("/login", data={"email": email, "password": password, "next": next_url}, follow_redirects=False)


def test_register_creates_student_and_logs_in(client):
    r = register(client)
    assert r.status_code == 303
    assert r.headers["location"] == "/"

    home = client.get("/")
    assert "Nguyễn Văn A" in home.text
    # A self-registered account is a student, never an admin.
    assert client.get("/admin").status_code == 403


def test_register_email_is_case_insensitive(client):
    assert register(client, email="Student@Example.com").status_code == 303
    client.post("/logout")
    r = register(client, email="student@example.COM")
    assert r.status_code == 400
    assert "đã được đăng ký" in r.text


def test_register_rejects_bad_input(client):
    assert "Email không hợp lệ" in register(client, email="not-an-email").text
    assert "ít nhất 8" in register(client, password="short").text
    assert "không khớp" in register(client, password="password123", confirm="password124").text


def test_login_with_uppercase_email(client, make_user):
    make_user("hs@example.com")
    r = login(client, "  HS@Example.com ")
    assert r.status_code == 303
    assert r.headers["location"] == "/"


def test_login_wrong_password_or_unknown_email(client, make_user):
    make_user("hs@example.com")
    for email, password in [("hs@example.com", "wrongpass1"), ("nobody@example.com", "password123")]:
        r = login(client, email, password)
        assert r.status_code == 400
        assert "Email hoặc mật khẩu không đúng" in r.text


def test_admin_login_goes_to_dashboard(client, make_user):
    make_user("admin@example.com", role=UserRole.ADMIN, name="Cô Admin")
    r = login(client, "admin@example.com")
    assert r.headers["location"] == "/admin"
    page = client.get("/admin")
    assert page.status_code == 200
    assert "Trang quản trị" in page.text


def test_anonymous_is_redirected_to_login(client):
    r = client.get("/admin", follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == "/login?next=/admin"


def test_login_respects_next_but_blocks_open_redirect(client, make_user):
    make_user("admin@example.com", role=UserRole.ADMIN)
    assert login(client, "admin@example.com", next_url="/admin").headers["location"] == "/admin"
    client.post("/logout")
    assert login(client, "admin@example.com", next_url="//evil.com").headers["location"] == "/admin"
    client.post("/logout")
    assert login(client, "admin@example.com", next_url="https://evil.com").headers["location"] == "/admin"


def test_logout_clears_session(client, make_user):
    make_user("admin@example.com", role=UserRole.ADMIN)
    login(client, "admin@example.com")
    client.post("/logout")
    assert client.get("/admin", follow_redirects=False).status_code == 303
