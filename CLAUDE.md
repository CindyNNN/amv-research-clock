# AI Investment Advisor Rules

This project builds a local research assistant for A shares and Hong Kong shares.

Rules:

- Treat all output as research support, not financial advice.
- Never place trades or request brokerage credentials.
- Always show data source, timestamp, and risk notes.
- Prefer explainable factors over opaque predictions.
- Focus the first board universe on technology and advanced manufacturing themes.
- When the user asks whether a sector, board, stock, or theme is worth buying, holding, reducing, avoiding, or "上车", load and follow `docs/skills/investment-advice-analysis/SKILL.md`.
- After analyzing a stock, sector, or theme, save or update an Obsidian-style Markdown note under `obsidian-vault/Investment-Research/` and link it from `obsidian-vault/Investment-Research/00_Index.md`.
- Record durable user preferences in `memory/Memory.md`.
- Record data quirks and mistakes in `memory/Learning.md`.
- Record shared definitions in `memory/Wiki.md`.
