# Работа с репозиторием

## Первый пуш (один раз)

Репозиторий уже инициализирован локально, есть коммит-база. Дальше:

```bash
# создать приватный репозиторий на GitHub и запушить текущую ветку
gh repo create captcha --private --source=. --remote=origin --push

# без gh — создать пустой репо в вебе, потом:
git remote add origin git@github.com:<твой-логин>/captcha.git
git branch -M main
git push -u origin main
```

## Доступ другу

**Приватный репозиторий на бесплатном плане GitHub:**

1. Открыть репозиторий → `Settings` → `Collaborators` → `Add people`.
2. Ввести GitHub-username или e-mail, выбрать роль **Write**.
3. Он принимает приглашение из письма/уведомления — и пушит напрямую в ветки.

Что ограничено на бесплатном плане для приватных репо: **protected branches** и
обязательные review в PR. Это тариф GitHub Pro ($4/мес) или Team. Пока проект в двоих
обходимся без защиты ветки, но правило «в `main` только через PR» соблюдаем руками.

## Имена веток и коммитов

```
main                 всегда рабочее состояние
feat/<corner>        новая возможность    feat/gateway-balance
fix/<corner>         баг                  fix/queue-dlq
refactor/<corner>    реструктуризация     refactor/src-layout
docs/<corner>        только документация
```

Коммит: `<тип>: <что сделано>` в императиве, 72 символа на строку темы.

```
feat: add solution projection per captcha type
fix: requeue task on solver timeout
refactor: collapse 19 packages into src/captcha
docs: describe layer contracts
```

## Ежедневный цикл

```bash
git switch main && git pull --rebase
git switch -c feat/pow-friendly
# ... работа ...
make lint imports test
git add -A && git commit -m "feat: friendly captcha pow solver"
git push -u origin feat/pow-friendly
# PR -> squash merge в main
```

Если работаете вдвоём в одних и тех же файлах — сначала `git pull --rebase`, никогда
`git merge` из main в ветку. Конфликты в `pyproject.toml` и `solution.py` решаются дольше
всего, поэтому оба файла меняются отдельными PR.

## Что не попадает в репозиторий

`.gitignore` уже закрывает: `.env`, `.venv/`, `models-cache/`, `data/`, веса (`*.pt`,
`*.onnx`, `*.safetensors`), аудио, большие дампы (`fixtures/**/bulk/`), `uv.lock`
(лок ставим отдельно, если захотим воспроизводимость CI — тогда убираем строку из .gitignore).

Секреты — никогда в код: ключи API, прокси с паролями, S3-ключи. Всё через `.env`
(см. `.env.example`) и переменные окружения в CI/докере.

## CI

`.github/workflows/ci.yml`: ruff → архитектурные контракты (`lint-imports`) → тесты
`tests/domain` и `tests/solvers/pow`. Тяжёлые CV-тесты подключаются отдельным job,
когда сойдёмся на образе с torch.

## Если что-то пошло не так

```bash
git status                  # что изменено
git diff --stat             # насколько
git switch -- .            # выбросить правки в рабочей копии
git restore --staged .     # снять со stage
git reflog                 # что было до неудачного rebase
```

Опасные команды (`reset --hard`, `clean -fdx`, `push --force`) — только с пониманием,
что в них теряется. В общую ветку `main` — никогда.
