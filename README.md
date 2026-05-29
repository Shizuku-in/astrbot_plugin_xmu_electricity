# 厦大电费查询 AstrBot 插件

通过厦门大学电费系统接口查询宿舍余额、最近用电和最近充值记录。

## 安装

把整个 `astrbot_plugin_xmu_electricity` 目录放到 AstrBot 的插件目录：

```text
AstrBot/data/plugins/astrbot_plugin_xmu_electricity
```

然后在 AstrBot WebUI 重载插件。

## 配置

在插件配置里填写：

- `cookie`: 抓包获取 `https://elec-app.xmu.edu.cn/sdk/getMe` 请求头中的 `Cookie`
- `ssbh`: 宿舍编号，可留空，插件会从 `/sdk/getMe` 自动读取

如果查询提示 Cookie 过期，请在微信里重新打开电费页面，再复制新的 Cookie。

## 命令

```text
/电费
/xmu_elec
```

返回内容包括：

- 当前余额
- 最近每日用电，按照明/空调汇总
- 最近 3 条充值记录
