from __future__ import annotations

import re
from collections import defaultdict
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
        """查询厦大宿舍电费余额、最近用电和充值记录。"""
        try:
            report = await self._build_report()
        except MissingCookieError as exc:
            report = str(exc)
        except AuthExpiredError:
            report = "电费查询失败：Cookie 可能已过期。请在微信里重新打开电费页面，抓取 /sdk/getMe 请求里的 Cookie 后更新插件配置。"
        except httpx.HTTPError as exc:
            logger.warning("XMU electricity request failed: %s", exc)
            report = f"电费查询失败：请求电费系统出错：{exc}"
        except Exception as exc:
            logger.exception("XMU electricity plugin failed")
            report = f"电费查询失败：{exc}"

        yield event.plain_result(report)

    @filter.command("xmu_elec")
    async def electricity_alias(self, event: AstrMessageEvent):
        """Alias for /电费."""
        async for result in self.electricity(event):
            yield result

    async def _build_report(self) -> str:
        cookie = str(self.config.get("cookie", "")).strip()
        if not cookie:
            raise MissingCookieError(
                "还没有配置电费系统 Cookie。请在 AstrBot 插件配置里填入 elec-app.xmu.edu.cn 请求头中的 Cookie。"
            )

        base_url = str(self.config.get("base_url", "https://elec-app.xmu.edu.cn")).rstrip("/")
        timeout = float(self.config.get("timeout_seconds", 10.0) or 10.0)
        page_size = int(self.config.get("page_size", 10) or 10)

        headers = {
            "Cookie": cookie,
            "Referer": f"{base_url}/sdk/bill",
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0 Safari/537.36 MicroMessenger"
            ),
            "Accept": "application/json, text/plain, */*",
        }

        async with httpx.AsyncClient(
            base_url=base_url,
            headers=headers,
            timeout=timeout,
            follow_redirects=False,
        ) as client:
            me = await self._get_json(client, "/sdk/getMe")
            ssbh = str(self.config.get("ssbh", "")).strip() or str(me.get("ssbh", "")).strip()
            if not ssbh:
                room = me.get("room") if isinstance(me.get("room"), dict) else {}
                ssbh = str(room.get("code", "")).strip()
            if not ssbh:
                raise RuntimeError("没有从 /sdk/getMe 里读到宿舍编号 ssbh。")

            usage = await self._get_json(
                client,
                "/sdk/getXfzd",
                params={"ssbh": ssbh, "pageNumber": 0, "pageSize": page_size},
            )
            recharges = await self._get_json(client, "/sdk/getCdzd", params={"ssbh": ssbh})

        return self._format_report(me, usage, recharges)

    async def _get_json(
        self,
        client: httpx.AsyncClient,
        path: str,
        params: dict[str, Any] | None = None,
    ) -> Any:
        response = await client.get(path, params=params)
        location = response.headers.get("location", "")
        content_type = response.headers.get("content-type", "")

        if response.status_code in {301, 302, 303, 307, 308} and "open.weixin.qq.com" in location:
            raise AuthExpiredError()
        if response.status_code in {401, 403}:
            raise AuthExpiredError()
        if "text/html" in content_type and "open.weixin.qq.com" in response.text:
            raise AuthExpiredError()

        response.raise_for_status()
        return response.json()

    def _format_report(self, me: dict[str, Any], usage: Any, recharges: Any) -> str:
        room = me.get("room") if isinstance(me.get("room"), dict) else {}
        building = room.get("bding") if isinstance(room.get("bding"), dict) else {}
        balance = room.get("syje", "未知")
        room_name = room.get("name") or room.get("code") or me.get("ssbh") or "未知房间"
        building_name = building.get("name") or room.get("building") or "未知楼栋"

        lines = [
            "电费查询结果",
            f"房间：{building_name} #{room_name}",
            f"当前余额：{balance} 元",
        ]

        usage_items = usage.get("content", []) if isinstance(usage, dict) else []
        if usage_items:
            lines.append("")
            lines.append("最近用电：")
            for day, values in self._summarize_usage(usage_items).items():
                detail = "，".join(f"{kind} {amount:g} 度" for kind, amount in values["by_kind"].items())
                lines.append(f"{day}：共 {values['total']:g} 度（{detail}）")

        if isinstance(recharges, list) and recharges:
            lines.append("")
            lines.append("最近充值：")
            for item in recharges[:3]:
                date = item.get("cdrq", "未知日期")
                time = item.get("cdxs", "")
                amount = item.get("jye", "未知")
                lines.append(f"{date} {time}：{amount} 元")

        return "\n".join(lines)

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
