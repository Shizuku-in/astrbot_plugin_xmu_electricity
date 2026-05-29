# 厦大电费查询插件

通过厦门大学电费系统接口查询宿舍余额、最近用电和最近充值记录，支持低电费预警。

## 配置

在插件配置里填写：

- `cookie`: 抓包获取 `https://elec-app.xmu.edu.cn/sdk/getMe` 请求头中的 `Cookie`
- `ssbh`: 宿舍编号，可留空，插件会从 `/sdk/getMe` 自动读取
- `warning_enabled`: 是否启用余额预警
- `warning_threshold`: 预警阈值，单位为元
- `warning_message`: 预警提示文本，支持 `{balance}` 和 `{threshold}` 占位符

如果查询提示 Cookie 过期，请在微信里重新打开电费页面，再复制新的 Cookie。

## 命令

```text
/电费
/xmu_elec
```

返回当前余额。启用预警后，如果余额低于阈值，会附带预警信息。

```text
/电费详细
```

返回当前余额、最近每日用电汇总和最近 3 条充值记录。

```text
/电费预警
```

只检查当前余额是否低于预警阈值。
