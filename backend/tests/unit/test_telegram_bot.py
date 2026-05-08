"""单元测试 — notifications/telegram_bot.py"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.notifications.telegram_bot import (
    CommandHandlers,
    TelegramCommandBot,
    parse_command,
)


# ---------------------------------------------------------------------------
# parse_command — 纯函数
# ---------------------------------------------------------------------------


class TestParseCommand:
    def test_none_or_empty(self):
        assert parse_command(None) is None
        assert parse_command("") is None
        assert parse_command("   ") is None

    def test_non_command(self):
        assert parse_command("hello") is None
        assert parse_command("balance") is None  # 缺前导斜杠

    def test_basic(self):
        assert parse_command("/balance") == "balance"
        assert parse_command("/Status") == "status"  # 大小写归一

    def test_with_args(self):
        assert parse_command("/balance some args") == "balance"

    def test_bot_suffix_match(self):
        assert (
            parse_command("/balance@DraculaAlert_bot", bot_username="DraculaAlert_bot")
            == "balance"
        )
        # 大小写不敏感
        assert (
            parse_command("/balance@DRACULAALERT_BOT", bot_username="DraculaAlert_bot")
            == "balance"
        )

    def test_bot_suffix_mismatch_returns_none(self):
        assert (
            parse_command("/balance@OtherBot", bot_username="DraculaAlert_bot") is None
        )

    def test_bot_suffix_no_username_configured(self):
        # 未配 bot_username 时不校验 suffix
        assert parse_command("/balance@anything") == "balance"

    def test_chinese_aliases(self):
        assert parse_command("/余额") == "balance"
        assert parse_command("/持仓") == "positions"
        assert parse_command("/暂停") == "pause"
        assert parse_command("/恢复") == "resume"
        assert parse_command("/状态") == "status"
        assert parse_command("/帮助") == "help"
        assert parse_command("/开始") == "start"

    def test_only_slash(self):
        assert parse_command("/") is None


# ---------------------------------------------------------------------------
# 测试用 fixture / helpers
# ---------------------------------------------------------------------------


def _handlers(**overrides) -> CommandHandlers:
    """构造一组 AsyncMock handlers，可按需覆盖。"""
    base = {
        "balance": AsyncMock(return_value="balance-ok"),
        "positions": AsyncMock(return_value="positions-ok"),
        "pause": AsyncMock(return_value="pause-ok"),
        "resume": AsyncMock(return_value="resume-ok"),
        "status": AsyncMock(return_value="status-ok"),
        "help": AsyncMock(return_value="help-ok"),
    }
    base.update(overrides)
    return CommandHandlers(**base)


def _bot(allowed=("12345",), handlers=None, **kwargs) -> TelegramCommandBot:
    return TelegramCommandBot(
        token="TEST:TOKEN",
        allowed_chat_ids=set(allowed),
        handlers=handlers or _handlers(),
        bot_username=kwargs.pop("bot_username", "DraculaAlert_bot"),
        http_client=kwargs.pop("http_client", MagicMock()),
        **kwargs,
    )


def _msg_update(update_id: int, chat_id: int | str, text: str) -> dict:
    return {
        "update_id": update_id,
        "message": {
            "message_id": 1,
            "chat": {"id": chat_id},
            "text": text,
        },
    }


# ---------------------------------------------------------------------------
# 构造校验
# ---------------------------------------------------------------------------


class TestConstruction:
    def test_empty_token_raises(self):
        with pytest.raises(ValueError, match="token"):
            TelegramCommandBot(token="", allowed_chat_ids={"1"}, handlers=_handlers())

    def test_empty_allowed_raises(self):
        with pytest.raises(ValueError, match="allowed_chat_ids"):
            TelegramCommandBot(
                token="t", allowed_chat_ids=set(), handlers=_handlers()
            )

    def test_allowed_normalized_to_str(self):
        bot = TelegramCommandBot(
            token="t",
            allowed_chat_ids={123, "456"},  # type: ignore[arg-type]
            handlers=_handlers(),
            http_client=MagicMock(),
        )
        assert bot._allowed == {"123", "456"}


# ---------------------------------------------------------------------------
# _handle_update — 派发逻辑
# ---------------------------------------------------------------------------


class TestHandleUpdate:
    @pytest.mark.asyncio
    async def test_dispatch_balance(self):
        h = _handlers()
        bot = _bot(handlers=h)
        bot._send = AsyncMock()  # type: ignore[method-assign]
        await bot._handle_update(_msg_update(1, 12345, "/balance"))
        h.balance.assert_awaited_once()
        bot._send.assert_awaited_once()
        chat_id, text = bot._send.await_args.args
        assert chat_id == "12345"
        assert text == "balance-ok"

    @pytest.mark.asyncio
    async def test_dispatch_chinese_alias(self):
        h = _handlers()
        bot = _bot(handlers=h)
        bot._send = AsyncMock()  # type: ignore[method-assign]
        await bot._handle_update(_msg_update(2, 12345, "/余额"))
        h.balance.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_unauthorized_chat_rejected(self):
        h = _handlers()
        bot = _bot(allowed=("12345",), handlers=h)
        bot._send = AsyncMock()  # type: ignore[method-assign]
        await bot._handle_update(_msg_update(1, 99999, "/balance"))
        h.balance.assert_not_called()
        bot._send.assert_awaited_once()
        chat_id, text = bot._send.await_args.args
        assert chat_id == "99999"
        assert "未授权" in text

    @pytest.mark.asyncio
    async def test_unknown_command(self):
        h = _handlers()
        bot = _bot(handlers=h)
        bot._send = AsyncMock()  # type: ignore[method-assign]
        await bot._handle_update(_msg_update(1, 12345, "/whatever"))
        h.balance.assert_not_called()
        h.help.assert_not_called()
        bot._send.assert_awaited_once()
        _, text = bot._send.await_args.args
        assert "未知命令" in text

    @pytest.mark.asyncio
    async def test_non_command_text_ignored(self):
        h = _handlers()
        bot = _bot(handlers=h)
        bot._send = AsyncMock()  # type: ignore[method-assign]
        await bot._handle_update(_msg_update(1, 12345, "hello there"))
        bot._send.assert_not_called()
        for handler in (h.balance, h.positions, h.pause, h.resume, h.status, h.help):
            handler.assert_not_called()

    @pytest.mark.asyncio
    async def test_start_routes_to_help(self):
        h = _handlers()
        bot = _bot(handlers=h)
        bot._send = AsyncMock()  # type: ignore[method-assign]
        await bot._handle_update(_msg_update(1, 12345, "/start"))
        h.help.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_handler_exception_falls_back_to_error_text(self):
        h = _handlers(balance=AsyncMock(side_effect=RuntimeError("boom")))
        bot = _bot(handlers=h)
        bot._send = AsyncMock()  # type: ignore[method-assign]
        await bot._handle_update(_msg_update(1, 12345, "/balance"))
        bot._send.assert_awaited_once()
        _, text = bot._send.await_args.args
        assert "失败" in text

    @pytest.mark.asyncio
    async def test_no_chat_id_silently_ignored(self):
        bot = _bot()
        bot._send = AsyncMock()  # type: ignore[method-assign]
        await bot._handle_update({"update_id": 1, "message": {"text": "/balance"}})
        bot._send.assert_not_called()

    @pytest.mark.asyncio
    async def test_edited_message_handled(self):
        h = _handlers()
        bot = _bot(handlers=h)
        bot._send = AsyncMock()  # type: ignore[method-assign]
        upd = {
            "update_id": 1,
            "edited_message": {
                "chat": {"id": 12345},
                "text": "/status",
            },
        }
        await bot._handle_update(upd)
        h.status.assert_awaited_once()


# ---------------------------------------------------------------------------
# _fetch_updates — HTTP 层
# ---------------------------------------------------------------------------


class TestFetchUpdates:
    @pytest.mark.asyncio
    async def test_returns_result_list(self):
        client = MagicMock()
        resp = MagicMock(status_code=200)
        resp.json.return_value = {
            "ok": True,
            "result": [_msg_update(1, 12345, "/balance")],
        }
        client.get = AsyncMock(return_value=resp)
        bot = _bot(http_client=client)
        out = await bot._fetch_updates()
        assert len(out) == 1
        assert out[0]["update_id"] == 1
        client.get.assert_awaited_once()
        params = client.get.await_args.kwargs["params"]
        assert params["offset"] == 0

    @pytest.mark.asyncio
    async def test_http_error_raises(self):
        client = MagicMock()
        resp = MagicMock(status_code=500, text="oops")
        client.get = AsyncMock(return_value=resp)
        bot = _bot(http_client=client)
        with pytest.raises(RuntimeError, match="HTTP 500"):
            await bot._fetch_updates()

    @pytest.mark.asyncio
    async def test_not_ok_raises(self):
        client = MagicMock()
        resp = MagicMock(status_code=200)
        resp.json.return_value = {"ok": False, "description": "bad token"}
        client.get = AsyncMock(return_value=resp)
        bot = _bot(http_client=client)
        with pytest.raises(RuntimeError, match="not ok"):
            await bot._fetch_updates()


# ---------------------------------------------------------------------------
# poll loop — 轻量集成
# ---------------------------------------------------------------------------


class TestPollLoop:
    @pytest.mark.asyncio
    async def test_offset_advances_after_handle(self):
        client = MagicMock()
        resp = MagicMock(status_code=200)
        resp.json.return_value = {
            "ok": True,
            "result": [
                _msg_update(10, 12345, "/balance"),
                _msg_update(11, 12345, "/status"),
            ],
        }
        client.get = AsyncMock(return_value=resp)
        h = _handlers()
        bot = _bot(handlers=h, http_client=client)
        bot._send = AsyncMock()  # type: ignore[method-assign]
        updates = await bot._fetch_updates()
        for u in updates:
            await bot._handle_update(u)
            uid = u.get("update_id")
            if uid >= bot._offset:
                bot._offset = uid + 1
        assert bot._offset == 12
        h.balance.assert_awaited_once()
        h.status.assert_awaited_once()


# ---------------------------------------------------------------------------
# _send — 出站
# ---------------------------------------------------------------------------


class TestSend:
    @pytest.mark.asyncio
    async def test_send_payload_shape(self):
        client = MagicMock()
        resp = MagicMock(status_code=200)
        client.post = AsyncMock(return_value=resp)
        bot = _bot(http_client=client)
        await bot._send("12345", "hi")
        client.post.assert_awaited_once()
        kwargs = client.post.await_args.kwargs
        body = kwargs["json"]
        assert body["chat_id"] == "12345"
        assert body["text"] == "hi"
        assert body["parse_mode"] == "HTML"
        assert body["disable_web_page_preview"] is True

    @pytest.mark.asyncio
    async def test_send_swallows_http_error(self):
        client = MagicMock()
        client.post = AsyncMock(side_effect=RuntimeError("network"))
        bot = _bot(http_client=client)
        # 不应抛
        await bot._send("12345", "hi")


# ---------------------------------------------------------------------------
# lifecycle
# ---------------------------------------------------------------------------


class TestLifecycle:
    @pytest.mark.asyncio
    async def test_start_then_stop(self):
        client = MagicMock()
        resp = MagicMock(status_code=200)
        resp.json.return_value = {"ok": True, "result": []}
        client.get = AsyncMock(return_value=resp)
        client.aclose = AsyncMock()
        bot = _bot(http_client=client)
        await bot.start()
        assert bot._task is not None
        await bot.stop()
        assert bot._task is None
