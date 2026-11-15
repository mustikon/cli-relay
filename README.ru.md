# cli-relay

**Один исполнитель поверх нескольких CLI-агентов с автоматическим переключением, когда у
подписки кончился лимит.**

[English version](README.md)

У вас подписки на Claude, ChatGPT и Google. У каждой свой CLI-агент и свои окна лимитов.
`cli-relay` отдаёт задачу первому провайдеру, который может её взять. Если на середине
задачи у него кончилась квота, работа переходит к следующему вместе с кратким отчётом о том,
что уже сделано.

```
cli-relay run "почини падающий тест в tests/test_api.py" --cwd ~/code/app --permission edit

[cli-relay] claude started (claude-opus-5-5)
...
[cli-relay] claude failed [rate_limit]: You've hit your limit · resets 3pm
[cli-relay] switching claude -> antigravity (rate_limit)
[cli-relay] antigravity started
...
[cli-relay] done by antigravity
```

## Зачем

* **Официальные CLI без модификаций.** `cli-relay` запускает `claude`, `agy` и `codex` так же,
  как вы сами, под вашим логином. OAuth-токены он не извлекает, не проксирует и не
  переиспользует. Свои права, хуки, `CLAUDE.md` / `AGENTS.md`, skills и MCP-серверы у каждого
  инструмента остаются на месте.
* **Знает про лимиты.** Claude Code сообщает в потоке событий загрузку пятичасового и
  недельного окна. `cli-relay` запоминает её и пропускает провайдера ещё *до* сбоя, если окно
  почти исчерпано. Провайдер, упавший на лимите, пропускается до времени сброса.
* **Передача задачи вместо перезапуска.** При переключении следующий агент получает исходную
  задачу, список уже сделанных действий и последний вывод. Ему также сказано сначала
  посмотреть `git status` / `git diff`.
* **Единый поток событий.** События всех провайдеров приведены к одному виду: `started`,
  `text_delta`, `tool_activity`, `quota_info`, `completed`, `failed`, плюс события самого реле
  `skipped` / `waiting` / `switched`. С флагом `--format ndjson` реле удобно вызывать из другой
  программы, например из голосового ассистента.
* **MCP-сервер.** `cli-relay mcp` даёт инструменты `relay_run` и `relay_status`, поэтому любой
  MCP-клиент может делегировать задачу и сразу получает переключение между провайдерами.
* **Маленький и без зависимостей.** Ядро использует только стандартную библиотеку Python. Для
  инструмента, который работает рядом со всеми вашими ключами, это важно.

## Провайдеры

| Тип | CLI / endpoint | Статус | Продолжение сессии | Телеметрия квот |
|---|---|---|---|---|
| `claude` | Claude Code `claude -p --output-format stream-json` | проверен вживую | `--resume` | окна five_hour / seven_day |
| `antigravity` | Google Antigravity `agy -p --output-format stream-json` | проверен вживую | `--conversation` | по тексту ошибки |
| `codex` | OpenAI Codex `codex exec --json` (на Windows находит и копию из десктопного приложения Codex) | флаги и обработка лимита проверены вживую на 0.167; успешный ответ — по документации | `exec resume` | по тексту ошибки, включая «try again at 11:05 AM» |
| `openai` | любой OpenAI-совместимый `/chat/completions` (OpenRouter, vLLM, Ollama, LM Studio…) | проверен на локальном сервере | — | HTTP 429 + `Retry-After` |

Тип `openai` — это только текстовый резерв: инструментов у него нет, файлы он править не умеет.

## Установка

```bash
pipx install cli-relay            # или: uv tool install cli-relay
pipx install "cli-relay[mcp]"     # вместе с MCP-сервером
```

Нужен Python 3.11+. Сначала установите нужные CLI-агенты и войдите в них.

## Использование

```bash
cli-relay status                                   # что установлено и у кого лимит
cli-relay run "объясни src/auth.py"                # по умолчанию только чтение
cli-relay run - < task.md --cwd ./repo --permission edit
cli-relay run "продолжай" --resume claude=SESSION_ID
cli-relay run "..." --providers antigravity,claude --format ndjson
cli-relay reset [provider]                         # забыть записанные лимиты
```

### Права

`--permission` переводится в собственную систему прав каждого CLI. Неинтерактивный запуск
никогда не ждёт подтверждения: всё, что потребовало бы вопроса, запрещается.

| `--permission` | Claude Code | Antigravity | Codex |
|---|---|---|---|
| `read_only` (по умолчанию) | `--permission-mode dontAsk` | `--mode plan` | `--sandbox read-only` |
| `edit` | `--permission-mode acceptEdits` | `--mode accept-edits` | `--sandbox workspace-write` |
| `full` | `--permission-mode auto` | `--mode auto` | `workspace-write` + `--approve-for-me` |

Настройка `permission` у провайдера задаёт верхнюю границу: задача никогда не получит больше
прав, чем там указано. Флаги «пропустить все проверки» `cli-relay` не использует.

## Настройка

Порядок поиска конфига:

1. `--config PATH`
2. `$CLI_RELAY_CONFIG`
3. `./cli-relay.toml`
4. `%APPDATA%\cli-relay\config.toml` на Windows или `~/.config/cli-relay/config.toml` в остальных системах

Без файла используется цепочка `claude → antigravity → codex`. Пример с комментариями лежит в
[`examples/cli-relay.toml`](examples/cli-relay.toml).

## Как MCP-сервер

```bash
claude mcp add cli-relay -- cli-relay mcp
```

По умолчанию MCP-клиент может запросить только `read_only`, что бы он ни просил. Иначе
агент в песочнице мог бы расширить свои права, делегировав задачу другому агенту. Разрешить
больше можно в конфиге: `[mcp] max_permission = "edit"`.

## Условия использования

`cli-relay` рассчитан на **ваши собственные** подписки на **вашем собственном** компьютере. Он
запускает официальный CLI каждого вендора так, как это сделал бы обычный пользователь, и
условия вендоров продолжают действовать. Для автоматизации без участия человека вендоры
рекомендуют API-ключи, а не вход по подписке. Не используйте `cli-relay`, чтобы делить одну
подписку на нескольких человек.

## Лицензия

[Apache-2.0](LICENSE)
