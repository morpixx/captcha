# Документация

Для ИИ-агентов — [AGENTS.md](../AGENTS.md) в корне: карта репозитория, правила слоёв,
рецепты «как добавить солвер/движок/адаптер», команды, definition of done. Модель
безопасности — [SECURITY.md](../SECURITY.md).

| файл | о чём |
|---|---|
| [architecture.md](architecture.md) | слои, четыре лейна, HTTP-first + NeedsBrowser, Camoufox, capabilities, canary, visual core |
| [protocol.md](protocol.md) | 2captcha-совместимый API: имена типов, прокси, hCaptcha, коды ошибок, форма решения |
| [plan.md](plan.md) | гейт 62/62, уровни L0–L3, пороги L3, Riot/Steam/Epic, роли, инфраструктура |
| [operations.md](operations.md) | эксплуатация: конфиг, health, метрики, бэкапы, откат, инциденты |
| [production-contract.md](production-contract.md) | закон для W1: лейны, ABI, ошибки, security, ownership |
| [baseline.md](baseline.md) | снимок состояния и известных неизвестных на 2026-11-15 |
| [hcaptcha-sites.md](hcaptcha-sites.md) | живая матрица hCaptcha: Steam/Riot/контроль, HTTP pass rate (владелец research-sites) |
| [adr-vision-architecture.md](adr-vision-architecture.md) | ADR-001: visual core + heads, лицензии, калибровка |
| [ml-research.md](ml-research.md) | обзор моделей, лицензий, VRAM/latency, датасетов |
| [ml.md](ml.md) | какие веса учить, формат артефактов, как подключить модель |
| [ml-tz.md](ml-tz.md) | ТЗ на ML: данные, обучение, метрики приёмки |
| [implementation-order.md](implementation-order.md) | порядок реализации 62 типов по тирам |
| [workflow.md](workflow.md) | git, GitHub, приватный репозиторий, доступ, CI |

Runbook-и на каждый тип и операционные — [runbooks/README.md](../runbooks/README.md).
