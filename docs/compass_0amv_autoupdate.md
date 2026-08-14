# 活跃市值(0AMV) 日常录入说明

研究辅助，非投资建议。0AMV 是指南针专有指数；**策略信号只需要涨跌百分比**（`amv_ret_1d` / 两日涨和），不必每天启动客户端。

## 日常怎么用（推荐）

收盘 / 盘中 / 同步 bat 都会：

1. **弹窗问你今日活跃市值收盘数值**
2. 相对昨收自动算涨跌幅，写入 `data/compass/0amv_*.csv/json`
3. 再发信号邮件（收盘任务还会推通达信）

手动命令：

```powershell
cd C:\Users\Cindy\Desktop\Finance\AI金融
python scripts\prompt_0amv_ret.py
# 或
python scripts\sync_compass_0amv.py --manual-only
```

无界面测试可用环境变量：

```powershell
$env:CYB_AMV_MANUAL_CLOSE="214125.4"
$env:CYB_AMV_MANUAL_DATE="2026-08-10"   # 可选
python scripts\prompt_0amv_ret.py
```

## 计划任务

| 时间 | 任务 | 作用 |
|---|---|---|
| 15:30 | Close | 弹窗录入收盘 → 收盘信号邮件 |

仅此一个工作日计划任务；14:40 盘中、15:35 同步、15:45 收盘任务已取消。

弹窗需要你在交互式桌面；取消则任务中止、不发信。

## 可选：仍用指南针自动灌盘

若偶尔要用客户端落盘：

```powershell
python scripts\sync_compass_0amv.py --timeout 420 --close
```

流程：启动 → 输入 `0AMV` → 必要时「下载→接收最新」→ 失败再弹窗。详见历史实现 `compass_ui.py` / `compass_autoupdate.py`。

## 输出文件

- `data/compass/0amv_daily.csv`
- `data/compass/0amv_indicators.csv`
- `data/compass/0amv_latest.json`（含 `ret_1d`、`ret_pct_input`）

## 限制

- 涨跌幅由你从指南针（或其它渠道）目视填入；推算收盘仅用于衔接历史序列与 MA
- 历史序列仍依赖已有 CSV；若从未导出过，需先有一份 `0amv_daily.csv`（可从旧指南针缓存导出一次）
- 研究辅助，非投资建议
