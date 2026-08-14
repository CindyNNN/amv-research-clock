# 创业板 AMV+情绪策略 QQ 邮件监控使用说明

## 工具判断的规则（当前线上策略）

对应稳健优化中的**得分冠军**骨架：`AMV入场 + 情绪/急跌离场 + MA60保护`。

买入信号：

- 模型当前空仓；
- 指南针活跃市值 **0AMV 两日涨和严格大于 3%**。

卖出信号（需同时满足「离场腿」且「未被 MA60 保护」）：

- 离场腿（任一）：
  - 0AMV 单日涨跌 **≤ -3.5%**；或
  - 市场情绪 **≥ 60**（过热）；
- 保护：若创业板指收盘 **高于 MA60**，则忽略上述离场（继续持有）。

工具每次运行都会发送一封 `BUY`、`SELL`、`HOLD`、`FLAT` 或 `ERROR`
状态邮件。信号在收盘后确认，只能作为下一交易日开盘执行参考；可交易标的建议用
创业板 ETF（如 159915）跟踪指数。

> 旧规则（情绪&lt;15 且 J&lt;30 买入 / KDJ死叉卖出）已停用，不再驱动邮件信号。

## 第一次配置 QQ 邮箱

1. 登录 QQ 邮箱网页版。
2. 在邮箱设置中开启 SMTP 服务。
3. 按 QQ 邮箱页面提示生成 SMTP 授权码。授权码不是 QQ 登录密码。
4. 在 Windows“编辑账户的环境变量”界面增加两个用户变量：

   - `CYB_QQ_EMAIL`：你的完整 QQ 邮箱，例如 `你的QQ号@qq.com`
   - `CYB_QQ_AUTH_CODE`：QQ 邮箱生成的 SMTP 授权码

也可以在 PowerShell 中设置用户变量：

```powershell
[Environment]::SetEnvironmentVariable(
  "CYB_QQ_EMAIL", "你的QQ号@qq.com", "User"
)
[Environment]::SetEnvironmentVariable(
  "CYB_QQ_AUTH_CODE", "你的QQ邮箱SMTP授权码", "User"
)
```

设置后关闭并重新打开终端。QQ SMTP 使用 `smtp.qq.com` 的 SSL 端口 `465`。

注意：Windows 用户环境变量会保存在当前 Windows 账户中。不要截图、发送或提交
真实授权码；如果怀疑泄露，请立即在 QQ 邮箱撤销旧授权码并生成新的授权码。

## 0AMV 数据依赖

邮件信号依赖本地指南针 0AMV 日线：

`data/compass/0amv_daily.csv`

若最新交易日缺少 0AMV，工具会报错并发送 ERROR 邮件。请先用：

```powershell
python scripts\sync_compass_0amv.py --timeout 420 --close
```

该脚本会自动启动指南针，并在缓存过期时点击「下载 → 接收最新」。详见 `docs/compass_0amv_autoupdate.md`。

## 先做不发邮件的预览

在本项目目录打开 PowerShell：

```powershell
$env:PYTHONPATH="$PWD\src"
python scripts\run_cyb_signal_monitor.py --dry-run
```

预览模式会更新行情/情绪并读取 0AMV，显示完整邮件正文，但不会连接 QQ SMTP，
也不会创建或修改策略状态文件。

## 正式运行

双击项目根目录的：

`run_cyb_signal_monitor.bat`

正常流程为：

1. 更新创业板指、市场情绪，并合并 0AMV；
2. 读取或重建模型状态（新策略使用独立状态文件）；
3. 判断当天信号；
4. 向同一个 QQ 邮箱发送状态邮件；
5. 邮件发送成功后保存模型状态。

建议在交易日 **15:45** 以后由计划任务自动跑收盘流程（内含 0AMV 同步）。
若手动跑收盘 bat，也会先同步 0AMV 再发信。

计划任务安装：

```powershell
powershell -ExecutionPolicy Bypass -File ".\scripts\install_cyb_monitor_tasks.ps1"
```

工作日：14:40 盘中预估 → 15:35 同步 0AMV → 15:45 收盘信号（再次同步后发信）。

## 状态文件

模型状态保存在：

`data/monitor/cyb_amv_emotion_strategy_state.json`

它记录的是策略模型状态，不是你的真实证券账户持仓。首次运行或旧情绪策略状态
不兼容时，工具会从历史回放重建。同一交易日重复运行仍会发送邮件，但不会重复
改变模型状态。

旧文件 `data/monitor/cyb_emotion_strategy_state.json` 不再使用。

若手工删除状态文件，下次运行会重新进行历史回放。状态文件不包含邮箱地址、
SMTP 授权码或任何券商信息。

## 常见退出码

- `0`：运行成功或预览成功。
- `2`：行情、情绪、0AMV 或指标数据处理失败；正常模式会尝试发送 ERROR 邮件。
- `3`：QQ SMTP 登录或发送失败，模型状态不会改变。
- `4`：缺少 QQ 邮箱环境变量或邮箱格式不正确。

若出现 SMTP 登录失败：

- 确认填写的是授权码而不是 QQ 密码；
- 确认 QQ 邮箱已开启 SMTP 服务；
- 重新打开终端，让用户环境变量生效；
- 如授权码已经撤销，生成新授权码后更新 `CYB_QQ_AUTH_CODE`。

## 盘中 / 收盘模式

- `--mode intraday`：盘中预估，不改正式状态；
- `--mode close`：收盘确认，成功发信后写入状态。

计划任务安装见 `scripts/install_cyb_monitor_tasks.ps1`。

## 风险提示

本工具仅用于量化研究支持，不构成投资建议。策略规则来自历史回测，存在过拟合与
失效风险；指数本身通常不可直接交易。
