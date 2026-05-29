from __future__ import annotations

import re
from collections import defaultdict
from decimal import Decimal, InvalidOperation
from typing import Any

import httpx
from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star


class XmuElectricityPlugin(Star):
    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.config = config

    @filter.command("电费")
    async def electricity(self, event: AstrMessageEvent):
        """查询当前电费余额。"""
        yield event.plain_result(await self._run_query(detail=False, alert_only=False))

    @filter.command("xmu_elec")
    async def electricity_alias(self, event: AstrMessageEvent):
        """Alias for /电费."""
        yield event.plain_result(await self._run_query(detail=False, alert_only=False))

    @filter.command("电费详细")
    async def electricity_detail(self, event: AstrMessageEvent):
        """查询当前余额、最近用电汇总和充值记录。"""
        yield event.plain_result(await self._run_query(detail=True, alert_only=False))

    @filter.command("电费预警")
    async def electricity_alert(self, event: AstrMessageEvent):
        """检查当前余额是否低于预警阈值。"""
        yield event.plain_result(await self._run_query(detail=False, alert_only=True))

    async def _run_query(self, detail: bool, alert_only: bool) -> str:
        try:
            report_data = await self._fetch_report_data(detail=detail)
            if alert_only:
                return self._format_alert_check(report_data["me"])
            return self._format_report(
                report_data["me"],
                report_data.get("usage"),
                report_data.get("recharges"),
                detail=detail,
            )
        except MissingCookieError as exc:
            return str(exc)
        except AuthExpiredError:
            return "电费查询失败：Cookie 可能已过期。请在微信里重新打开电费页面，抓取 /sdk/getMe 请求里的 Cookie 后更新插件配置。"
        except httpx.HTTPError as exc:
            logger.warning("XMU electricity request failed: %s", exc)
            return f"电费查询失败：请求电费系统出错：{exc}"
        except Exception as exc:
            logger.exception("XMU electricity plugin failed")
            return f"电费查询失败：{exc}"

    async def _fetch_report_data(self, detail: bool) -> dict[str, Any]:
        cookie = str(self.config.get("cookie", "")).strip()
        if not cookie:
            raise MissingCookieError(
                "还没有配置电费系统 Cookie。请在 AstrBot 插件配置里填入 elec-app.xmu.edu.cn 请求头中的 Cookie。"
            )

        base_url = str(self.config.get("base_url", "https://elec-app.xmu.edu.cn")).rstrip("/")
        timeout = float(self.config.get("timeout_seconds", 10.0) or 10.0)
        page_size = int(self.config.get("page_size", 10) or 10)
        user_agent = str(self.config.get("user_agent", "")).strip() or (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0 Safari/537.36 MicroMessenger"
        )

        headers = {
            "Cookie": cookie,
            "Referer": f"{base_url}/sdk/bill",
            "User-Agent": user_agent,
            "Accept": "application/json, text/plain, */*",
        }

        async with httpx.AsyncClient(
            base_url=base_url,
            headers=headers,
            timeout=timeout,
            follow_redirects=False,
        ) as client:
            me = await self._get_json(client, "/sdk/getMe")
            data: dict[str, Any] = {"me": me}

            if detail:
                ssbh = self._get_ssbh(me)
                data["usage"] = await self._get_json(
                    client,
                    "/sdk/getXfzd",
                    params={"ssbh": ssbh, "pageNumber": 0, "pageSize": page_size},
                )
                data["recharges"] = await self._get_json(client, "/sdk/getCdzd", params={"ssbh": ssbh})

        return data

    async def _get_json(
        self,
        client: httpx.AsyncClient,
        path: str,
        params: dict[str, Any] | None = None,
    ) -> Any:
        response = await client.get(path, params=params)
        location = response.headers.get("location", "")
        content_type = response.headers.get("content-type", "")

        if response.status_code in {301, 302, 303, 307, 308} and self._is_auth_redirect(location):
            raise AuthExpiredError()
        if response.status_code in {401, 403}:
            raise AuthExpiredError()
        if "text/html" in content_type and "open.weixin.qq.com" in response.text:
            raise AuthExpiredError()

        response.raise_for_status()
        return response.json()

    def _is_auth_redirect(self, location: str) -> bool:
        return (
            "open.weixin.qq.com" in location
            or "/auth/gettoken" in location
            or "/auth/getuser" in location
        )

    def _format_report(self, me: dict[str, Any], usage: Any, recharges: Any, detail: bool) -> str:
        room_info = self._room_info(me)
        lines = [
            "电费查询结果",
            f"房间：{room_info['building_name']} #{room_info['room_name']}",
            f"当前余额：{room_info['balance_text']} 元",
        ]

        alert = self._build_alert_message(room_info["balance"])
        if alert:
            lines.append(alert)

        if not detail:
            return "\n".join(lines)

        usage_items = usage.get("content", []) if isinstance(usage, dict) else []
        if usage_items:
            lines.append("")
            lines.append("最近用电：")
            for day, values in self._summarize_usage(usage_items).items():
                detail_text = "，".join(f"{kind} {amount:g} 度" for kind, amount in values["by_kind"].items())
                lines.append(f"{day}：共 {values['total']:g} 度（{detail_text}）")
        else:
            lines.append("")
            lines.append("最近用电：暂无数据")

        if isinstance(recharges, list) and recharges:
            lines.append("")
            lines.append("最近充值：")
            for item in recharges[:3]:
                date = item.get("cdrq", "未知日期")
                time = item.get("cdxs", "")
                amount = item.get("jye", "未知")
                lines.append(f"{date} {time}：{amount} 元")
        else:
            lines.append("")
            lines.append("最近充值：暂无数据")

        return "\n".join(lines)

    def _format_alert_check(self, me: dict[str, Any]) -> str:
        room_info = self._room_info(me)
        alert = self._build_alert_message(room_info["balance"])
        if alert:
            return "\n".join(
                [
                    "电费预警",
                    f"房间：{room_info['building_name']} #{room_info['room_name']}",
                    f"当前余额：{room_info['balance_text']} 元",
                    alert,
                ]
            )

        threshold = self._warning_threshold()
        if threshold is None:
            return "电费预警未启用：请在插件配置里开启 warning_enabled 并设置 warning_threshold。"

        return "\n".join(
            [
                "电费预警",
                f"房间：{room_info['building_name']} #{room_info['room_name']}",
                f"当前余额：{room_info['balance_text']} 元",
                f"状态：余额不低于预警阈值 {threshold:g} 元。",
            ]
        )

    def _room_info(self, me: dict[str, Any]) -> dict[str, Any]:
        room = me.get("room") if isinstance(me.get("room"), dict) else {}
        building = room.get("bding") if isinstance(room.get("bding"), dict) else {}
        balance = self._to_decimal(room.get("syje"))
        balance_text = f"{balance:g}" if balance is not None else "未知"

        return {
            "balance": balance,
            "balance_text": balance_text,
            "room_name": room.get("name") or room.get("code") or me.get("ssbh") or "未知房间",
            "building_name": building.get("name") or room.get("building") or "未知楼栋",
        }

    def _get_ssbh(self, me: dict[str, Any]) -> str:
        ssbh = str(self.config.get("ssbh", "")).strip() or str(me.get("ssbh", "")).strip()
        if not ssbh:
            room = me.get("room") if isinstance(me.get("room"), dict) else {}
            ssbh = str(room.get("code", "")).strip()
        if not ssbh:
            raise RuntimeError("没有从 /sdk/getMe 里读到宿舍编号 ssbh。")
        return ssbh

    def _build_alert_message(self, balance: Decimal | None) -> str:
        threshold = self._warning_threshold()
        if threshold is None or balance is None or balance >= threshold:
            return ""

        template = str(
            self.config.get(
                "warning_message",
                "电费预警：当前余额 {balance} 元，已低于预警阈值 {threshold} 元。",
            )
        )
        try:
            return template.format(balance=f"{balance:g}", threshold=f"{threshold:g}")
        except (KeyError, ValueError):
            return f"电费预警：当前余额 {balance:g} 元，已低于预警阈值 {threshold:g} 元。"

    def _warning_threshold(self) -> Decimal | None:
        enabled = self._config_bool(self.config.get("warning_enabled", False))
        if not enabled:
            return None

        threshold = self._to_decimal(self.config.get("warning_threshold"))
        if threshold is None or threshold <= 0:
            return None
        return threshold

    def _config_bool(self, value: Any) -> bool:
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            return value.strip().lower() in {"1", "true", "yes", "on", "启用", "是"}
        return bool(value)

    def _to_decimal(self, value: Any) -> Decimal | None:
        if value is None or value == "":
            return None
        try:
            return Decimal(str(value))
        except (InvalidOperation, ValueError):
            return None

    def _summarize_usage(self, usage_items: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
        summary: dict[str, dict[str, Any]] = {}
        for item in usage_items:
            day = str(item.get("cjrq") or "未知日期")
            kind = self._extract_meter_kind(str(item.get("ssmc") or "用电"))
            amount = float(item.get("zyl") or 0)
            bucket = summary.setdefault(day, {"total": 0.0, "by_kind": defaultdict(float)})
            bucket["total"] += amount
            bucket["by_kind"][kind] += amount
        return summary

    def _extract_meter_kind(self, name: str) -> str:
        match = re.search(r"\[([^\]]+)\]\s*$", name)
        return match.group(1) if match else name

    async def terminate(self):
        pass


class MissingCookieError(Exception):
    pass


class AuthExpiredError(Exception):
    pass
