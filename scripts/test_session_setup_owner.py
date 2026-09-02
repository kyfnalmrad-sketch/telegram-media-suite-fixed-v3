from bot_service import TelegramBotService

service = TelegramBotService(
    lambda: None,
    lambda: "/tmp",
    lambda text: None,
    self_enrollment_enabled=False,
    session_setup_owner_id=2104850156,
)

assert service._is_session_owner(2104850156)
assert not service._is_session_owner(2104850157)
assert "رمز التحقق" in service._session_setup_prompt("code")
assert "كلمة مرور" in service._session_setup_prompt("password")
print("SESSION_SETUP_OWNER_OK")
