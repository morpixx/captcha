# Browser session memory

Замер 2026-11-15, Camoufox 0.5.6, headless, Windows, свежий временный профиль.
Скрипт: `tools/browser_memory_probe.py`.

Считается RSS всего дерева процессов, которое запустил Camoufox (Firefox +
content-процессы + node-драйвер). Сам Python-процесс probe исключён: это не
браузерная сессия.

| фаза | RSS | процессов |
|---|---:|---:|
| сессия открыта, страница не загружена | **634.7 MB** | 8 |
| `https://example.com/` загружена | **663.9 MB** | 8 |
| бюджет | 700 MB | — |
| вердикт | **pass** | — |

Это подтверждает оценку «~700 MB на сессию», а не 1.3–1.5 GB. При
`WORKER_BROWSER_SESSIONS=12` закладывать ~8 GB RSS на браузерный worker плюс
запас на Python/очередь.

Оговорки:
- один снимок, не пик под тяжёлой страницей; для L3 мерить на реальном целевом
  сайте и под нагрузкой;
- Windows RSS отличается от Linux/cgroup в browser-образе;
- `node.exe` в дереве — драйвер/скрипт страницы, он входит в бюджет сессии.

Команда:

```powershell
D:/captcha/.venv/Scripts/python.exe tools/browser_memory_probe.py
```
