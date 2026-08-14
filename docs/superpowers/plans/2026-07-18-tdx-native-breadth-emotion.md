# 通达信纯公式全市场情绪副图 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 用通达信原生历史涨跌家数函数替换失效的 TQ 序列副图，并标注情绪策略买卖点。

**Architecture:** `QXBASE` 作为只输出 `ADVANCE/DECLINE` 的跨证券引用接口；`CYBQX` 通过 `CALCSTOCKINDEX` 合计上海、深圳和北证50的历史涨跌家数，计算情绪、KDJ与近似回撤规则，再用 `TFILTER`配对买卖信号。两个公式均为纯文本，不依赖任何运行进程或外部文件。

**Tech Stack:** 通达信公式语言、pytest静态契约测试。

## Global Constraints

- `CYBQX` 不得包含 `SIGNALS_TQ`、`SIGNALS_USER`、`EXTERNVALUE` 或 Python 路径。
- 不得包含 `ORDERBUY`、`ORDERSELL`、`BUY(`、`SELL(` 等交易调用。
- 市场代码固定为 `SH000001`、`SZ399001`、`BJ899050`。
- 情绪口径固定为上涨 /（上涨 + 下跌）×100，数据缺失时返回 `DRAWNULL`。
- 买入为情绪<15且J<30；卖出为KDJ死叉且收盘低于MA20，或最近原始买点后的最高收盘回撤8%。
- 使用 `TFILTER(...,0)` 过滤并配对信号。
- 当前目录不是有效Git仓库，不创建工作树或提交。

---

### Task 1: 创建 QXBASE 历史涨跌家数接口

**Files:**
- Create: `tdx_indicators/QXBASE_市场涨跌家数.txt`
- Create: `tests/test_tdx_native_formula.py`

**Interfaces:**
- Produces output 1: `上涨:ADVANCE`。
- Produces output 2: `下跌:DECLINE`。

- [ ] **Step 1: Write failing helper formula test**

```python
def test_qxbase_has_two_ordered_breadth_outputs():
    source = QXBASE.read_text(encoding="utf-8")
    assert "上涨:ADVANCE;" in source
    assert "下跌:DECLINE;" in source
    assert source.index("上涨:ADVANCE;") < source.index("下跌:DECLINE;")
```

- [ ] **Step 2: Verify RED**

Run: `python -m pytest tests/test_tdx_native_formula.py -q`
Expected: FAIL because `QXBASE_市场涨跌家数.txt` does not exist.

- [ ] **Step 3: Implement QXBASE**

Create a minimal two-output technical indicator with only comments and the two ordered outputs.

- [ ] **Step 4: Verify GREEN**

Run: `python -m pytest tests/test_tdx_native_formula.py -q`
Expected: QXBASE test PASS.

### Task 2: Replace CYBQX with native breadth formula

**Files:**
- Modify: `tdx_indicators/CYBQX_创业板情绪副图.txt`
- Modify: `tests/test_tdx_native_formula.py`
- Modify: `tests/test_tdx_indicator_formula.py`

**Interfaces:**
- Consumes: outputs 1 and 2 of `QXBASE` for three fixed index codes.
- Produces: emotion curve, threshold, breadth diagnostics, KDJ diagnostic, paired buy/sell markers.

- [ ] **Step 1: Write failing native formula contract tests**

```python
def test_cybqx_uses_native_cross_index_breadth():
    source = CYBQX.read_text(encoding="utf-8")
    for code in ("SH000001", "SZ399001", "BJ899050"):
        assert source.count(f"CALCSTOCKINDEX('{code}','QXBASE'") == 2
    assert "SIGNALS_TQ" not in source
    assert "TFILTER(" in source
```

- [ ] **Step 2: Verify RED**

Run: `python -m pytest tests/test_tdx_native_formula.py -q`
Expected: FAIL because current CYBQX still uses `SIGNALS_TQ`.

- [ ] **Step 3: Implement breadth and emotion**

Read both QXBASE outputs for each market, sum into `UPN/DNN`, calculate `EMO` only for positive denominators, and output the yellow emotion line, white threshold, and NODRAW up/down counts.

- [ ] **Step 4: Implement KDJ and paired signals**

Calculate standard KDJ, raw buy, variable-window peak since the most recent raw buy, two raw sell conditions, and `TFILTER(BUYRAW,SELLRAW,0)`. Draw buy/sell icons and text at bounded emotion-axis positions.

- [ ] **Step 5: Update legacy formula test**

Replace the obsolete requirement for `SIGNALS_TQ(1..6,2)` with explicit assertions that TQ/external/trading functions are absent.

- [ ] **Step 6: Verify formula contracts**

Run: `python -m pytest tests/test_tdx_native_formula.py tests/test_tdx_indicator_formula.py -q`
Expected: PASS.

### Task 3: Documentation and verification

**Files:**
- Modify: `docs/cyb_email_monitor_usage.md`

**Interfaces:**
- Produces: exact two-formula installation order and local data download requirements.

- [ ] **Step 1: Replace TQ recommendation**

Document `QXBASE` first, `CYBQX` second, and downloading daily history for the three reference indices. Mark the TQ push method as deprecated for this indicator.

- [ ] **Step 2: Run full test suite**

Run: `python -m pytest -q`
Expected: all tests PASS.

- [ ] **Step 3: Static safety audit**

Run: `rg -n -i "SIGNALS_TQ|SIGNALS_USER|EXTERNVALUE|ORDERBUY|ORDERSELL" tdx_indicators/QXBASE_市场涨跌家数.txt tdx_indicators/CYBQX_创业板情绪副图.txt`
Expected: no matches.

- [ ] **Step 4: User-side formula compilation**

The user pastes and tests `QXBASE` first, then `CYBQX`. If the editor reports a line-specific formula error, capture the exact error and fix through a regression test before changing the formula.
