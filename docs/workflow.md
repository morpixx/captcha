# Работа с репозиторием

Поток изменений, защита ветки, CI и договорённости на двоих.

## 0. Один раз после клона

```bash
git clone git@github.com:<логин>/captcha.git && cd captcha
uv sync --extra dev        # окружение; venv активировать не нужно, всё через uv run
git config user.name / user.email   # если глобально ещё не настроены
```

## 1. Флоу: от задачи до main

```
        ┌──────────────────────────────────────────────────────────────┐
        │  main — защищён ruleset'ом: только через PR, только зелёный  │
        └──────────────────────────────────────────────────────────────┘
              ▲                                        │
              │ squash merge                           │ git pull --rebase
              │                                        ▼
        ┌─────────────┐   PR + CI    ┌──────────────────────────────┐
        │ pull request │◄────────────│ feat/<тема> (живёт 1-3 дня)  │
        └─────────────┘              └──────────────────────────────┘
              ▲                                        ▲
              │ approval напарника                     │ push
              │                                        │
        ┌──────────────────────────────────────────────────────────────┐
        │ локально: make lint imports test                             │
        └──────────────────────────────────────────────────────────────┘
```

1. **Ветка по смыслу:** `feat/queue-dlq`, `fix/solution-cf-clearance`, `docs/workflow`.
   Одна ветка — одна тема.
2. **Синхронизация перед стартом:**
   ```bash
   git switch main && git pull --rebase
   git switch -c feat/<тема>
   ```
3. **Мелкие коммиты** в императиве: `feat:`, `fix:`, `refactor:`, `docs:`, `chore:`.
4. **Локальные проверки — те же, что в CI:**
   ```bash
   make lint imports test
   ```
5. **Push и PR:** `git push -u origin feat/<тема>` → `gh pr create --fill`.
   Шаблон PR (`.github/pull_request_template.md`) подставит чеклист.
6. **Ревью:** напарник читает PR, комментирует по строкам. Без approval ruleset не даст мержить.
7. **Squash merge** — в `main` один коммит на фичу.
8. **После мержа:** ветку удалить, у себя `git switch main && git pull --rebase`.
9. **Хотфикс:** та же схема с `fix/<что>`, но approval можно получить постфактум — ruleset
   это разрешает, а привычка пушить в main напрямую — нет.

## 2. Ветки и коммиты

```
main                 всегда зелёный, защищён
feat/<corner>        новая возможность
fix/<corner>         баг
refactor/<corner>    без изменения поведения
docs/<corner>        документация
chore/<corner>       ci, конфиги, зависимости
```

```
feat: add solution projection per captcha type
fix: requeue task on solver timeout
refactor: collapse packages into src/captcha
docs: describe layer contracts
chore(ci): pin python 3.14
```

## 3. CI: что проверяется

`.github/workflows/ci.yml` на каждый PR и пуш в main:

| шаг | что ловит |
|---|---|
| `uv sync --extra dev` | сломанный `pyproject.toml`, несуществующую версию пакета |
| `ruff check` | стиль, мёртвые импорты, неиспользуемые noqa |
| `lint-imports` | нарушение слоёв: домен потянул redis, солвер — адаптер |
| `pytest` | `tests/domain` + `tests/solvers/pow`; CV-тесты отдельным job позже |

## 4. Защита main

Репозиторий публичный, поэтому защита ветки на бесплатном плане доступна целиком:
rulesets, require PR, require approval, require status checks, block force-push.

Проверка перед публикацией (сделана): `.env` не в индексе, ключей в истории коммитов нет.

Создание и включение:

```bash
gh repo create captcha --public --source=. --remote=origin --push
```

```text
Settings → Rules → Rulesets → New branch ruleset
  Target: main
  ✔ Require a pull request before merging   (Required approvals: 1)
  ✔ Require status checks to pass           (check: "checks")
  ✔ Block force pushes
  ✔ Restrict deletions
```

Имя чека берётся из списка проверок в PR — там будет `checks` (job из `ci.yml`).

Если однажды понадобится приватность: GitHub Pro ($4/мес на человека) сохраняет тот же
ruleset, либо GitLab Free даёт protected branches на приватных бесплатно.

## 5. Где будут конфликты

| файл | почему | как уменьшить |
|---|---|---|
| `pyproject.toml` | оба добавляют зависимости | отдельный PR на зависимость |
| `domain/enums.py`, `domain/solution.py` | оба добавляют типы | сначала PR с типом, потом код на него |
| `docs/architecture.md` | оба правят дерево | правки дерева — в PR, который меняет дерево |
| `docker-compose.yml` | оба добавляют сервис | сервис = отдельный PR |

Конфликт чинит автор ветки. Всегда `git pull --rebase`, никогда `git merge main` в ветку.

## 6. Ревью: минимальный чеклист

- [ ] изменение делает одну вещь, и она названа в заголовке PR;
- [ ] `make lint imports test` зелёные (то же в CI);
- [ ] новые импорты не нарушают границы слоёв;
- [ ] новый капча-тип → обновлён `runbooks/` и `domain/enums.py`;
- [ ] менялось поведение API → обновлён `docs/protocol.md`;
- [ ] нет секретов, путей вида `C:\\Users\\...`, закомментированного кода.

## 7. Окружение у обоих одинаковое

- Python пинится `.python-version` (3.14), `uv` поставит его сам;
- `uv sync --extra dev` поднимает окружение, активация venv не нужна;
- всё запускается через `uv run ...`;
- `.vscode/` в репозитории: тема иконок, иконки папок, скрытие кэшей, ruff на сохранении.

## 8. Что не попадает в репозиторий

`.gitignore` закрывает `.env`, `.venv/`, `models-cache/`, `data/`, веса (`*.pt`, `*.onnx`,
`*.safetensors`), аудио, большие дампы (`fixtures/**/bulk/`), кэши линтеров и
`.import_linter_cache/`. Секреты — только через `.env` (см. `.env.example`) и переменные
окружения в CI/докере.

`uv.lock` сейчас в `.gitignore`. Когда понадобится воспроизводимость в CI — убрать строку
и закоммитить лок.

## 9. Первый пуш и доступ напарнику

```bash
gh repo create captcha --public --source=. --remote=origin --push
```

Доступ: `Settings` → `Collaborators` → `Add people`, роль **Write**.

## 10. Если что-то пошло не так

```bash
git status                  # что изменено
git diff --stat             # насколько
git switch -- .             # выбросить правки в рабочей копии
git restore --staged .      # снять со stage
git reflog                  # что было до неудачного rebase
git log --oneline --graph -10
```

Опасные команды (`reset --hard`, `clean -fdx`, `push --force`) — только с пониманием, что
в них теряется. В `main` force-push запрещён ruleset'ом.

Падает только у напарника — почти всегда окружение: `uv sync --extra dev`, при совсем
странном поведении `rm -rf .venv` и синк заново. Падает у обоих — баг в `main`, чинится
веткой `fix/`, не откатом.
