# ML: что учить и как подключить

Код классификации, детекции, регрессора поворота и custom-типа hopscotch готов.
Не хватает только весов. Этот файл — контракт между обучением и сервисом: имя
артефакта, формат, как положить, как проверить, что воркер его увидел.

Ничего в коде под конкретную архитектуру не подгоняется: солверы просят артефакт по
имени, реестр отдаёт файл или каталог, загрузчик собирает модель. Одиночный артефакт
это `<name>/<version>/weights.<ext>`; bundle — каталог из нескольких файлов
(`model.safetensors`, `config.json`, `release.json`), у каждого свой sha256 в
`meta.json`.

## Полный список артефактов

| артефакт | формат | закрывает типов |
|---|---|---|
| `rotate-v1` | ResNet50 `state_dict`, последний слой `fc` → 2 (`sin θ`, `cos θ`) | `rotate`, `captchafox` |
| `click-v1` | YOLO (`.pt`), `model.names` = названия объектов | `click`, `coordinates`, `hunt`, `basilisk`, `atb`, `mtcaptcha`, `yidun`, `tencent` — 9 штук |
| `bbox-v1` | YOLO | `bounding_box` |
| `drag-v1` | YOLO, классы источника и цели | `drag_drop` |
| `draw-v1` | YOLO | `draw_around` |
| `hcaptcha-v1` | safetensors-голова | `hcaptcha`, `hcaptcha_enterprise`, `classification_grid` |
| `recaptcha-v1` | safetensors-голова | `recaptcha_v2` (grid), `classification_grid` |
| `funcap-v1` | safetensors-голова, сетка 2×3 | `funcaptcha`, `funcaptcha_enterprise` |
| `awswaf-v1` | safetensors-голова | `awswaf` |
| `shein-v1` | safetensors-голова | `shein` |
| `hopscotch-v1` | bundle: `model.safetensors` (DINOv3 ViT-B/16 + query head) + `config.json` + `release.json` (калибровка/rejector) | `hopscotch` (custom, вне 62) |

Актуальный список из кода: `make expected-models` — `hopscotch-v1` объявлен там как
`layout=bundle`, поэтому check-model проверяет его пофайлово, а не как один блоб.

Окружение: bundle-головы и ONNX-артефакты (rotate/detection) требуют `--extra cv`;
в нём уже есть `torch`, `torchvision`, `opencv-python-headless`, `safetensors`,
`onnxruntime` и `transformers`.

## Голова классификации

Код читает ровно это (`solvers/classification/head.py`):

```
weights.safetensors
  classifier.weight   float32 [C, 768]   # C = число классов, D = размерность ViT-фичей
  classifier.bias     float32 [C]
meta.json
  {
    "name": "hcaptcha-v1",
    "version": "v1",
    "framework": "safetensors",
    "s3_key": "hcaptcha-v1/v1/weights.safetensors",
    "sha256": "<hex>",
    "labels": {"bus": 0, "crosswalk": 1, ...},   # имя -> индекс, порядок = порядок строк в W
    "threshold": 0.5,
    "base_backbone": "google/vit-base-patch16-224-in21k"
  }
```

Что важно:

- **LoRA не нужна.** `_materialize_head` читает только `classifier.*`; мерж LoRA-дельт
  в `head.py` помечен `FILL` и не реализован. Учи голову на замороженном backbone.
- **Backbone заморожен**: ViT-B/16 выдаёт 768-мерный пул на тайл. Один проход по датасету
  кэширует фичи, дальше учится линейная модель (логистическая регрессия на класс).
- **Порог** берётся из `meta.json` (`threshold`), иначе 0.5. Код матчит `prompt` →
  `label` точно, потом пробует единственное/множественное число; промпт с артиклем
  (`"a bus"`) не найдётся.
- **labels[] порядок** = порядок строк матрицы. Перепутал — ветки выбираются наугад,
  и ошибки не будет: при пустом предсказании код всё равно отдаст argmax.

## YOLO-детекторы

```python
from ultralytics import YOLO
model = YOLO("yolov8n.pt")
model.train(data="click.yaml", imgsz=640, epochs=100)
```

Контракт: обычный `.pt` от ultralytics, `model.names` = словарь классов. Код матчит
`task.payload["target"]` против `model.names`: точное совпадение → подстрока → самый
короткий вариант. Имена классов пиши так, как они приходят в промптах: `bus`,
`traffic light`, `crosswalk`. Ошибка в имени = `ERROR_CAPTCHA_UNSOLVABLE` на каждом
запросе.

## Регрессор поворота

```python
import torch
from torchvision.models import resnet50
m = resnet50(weights=None)
m.fc = torch.nn.Linear(m.fc.in_features, 2)   # (sin, cos)
# ... обучение ...
torch.save(m.state_dict(), "rotate-v1.pt")
```

Загрузчик делает ровно `resnet50(weights=None)` + `load_state_dict` + препроцесс
`ResNet50_Weights.DEFAULT`. Датасет синтетический: крути любые картинки на случайный
угол, таргет — единичный вектор `(sin θ, cos θ)`, лосс косинусный. Точности ±2° хватает.

## Как подключить

```bash
# 1. одиночный артефакт: положить в бакет (bucket/name/version/{weights.*, meta.json} + name/latest)
make publish-model NAME=rotate-v1 VERSION=v1 PATH=./rotate-v1.pt

# bundle: PATH — каталог, внутри model.safetensors + config.json + release.json
# (+ необязательный checksums.json). Роутится в Registry.publish_artifact.
make publish-model NAME=hopscotch-v1 VERSION=v1 PATH=./artifacts/hopscotch-v1/v1

# 2. проверить: файл/каждый файл и sha256
make check-model NAME=rotate-v1 VERSION=v1
make check-model NAME=hopscotch-v1 VERSION=v1

# тот же вызов, но с попыткой загрузить веса своим загрузчиком
make check-model NAME=rotate-v1 VERSION=v1 LOAD=1

# 3. посмотреть, что реально лежит в бакете
make models
```

`publish-model` кладёт объекты по той же раскладке, которую читает `Registry.download`:
`<bucket>/<name>/<version>/weights.<ext>`, `.../meta.json`, `<name>/latest` (тело = версия).
Указывать `sha256` руками не нужно — считается по файлу.

Для bundle `meta.json` получает `layout="bundle"`, `artifact` и `files={имя: sha256}`;
`check-model` идёт через `Registry.check_artifact`: скачивает каждый файл, сверяет
его sha256, проверяет обязательный набор и что JSON-сайдкары парсятся. Артефакт без
манифеста (старые публикации) проверяется по sha256 только для весов и получает
предупреждение. `download()` для bundle материализует и проверяет весь каталог, а
возвращает primary-файл; `get_artifact()` отдаёт каталог целиком — это то, что читает
загрузчик bundle-солвера.

Дальше воркер ничего перезапускать не обязан: `get_meta(name, "latest")` читается на
каждом вызове, а `Registry.watch()` умеет ловить смену версии в фоне.

## Что проверить после публикации

1. `make check-model ... LOAD=1` — файл скачался, sha сошёлся, загрузчик не упал.
2. Задача через API: `type` из таблицы выше, `payload` минимальный (`image_b64` для
   голов и детекторов, `bg`/`piece` для слайдера).
3. В логах воркера: `solver.ready` для нужного типа и `task.done` без
   `ERROR_MODEL_INFERENCE`.

## Чего учить не надо

| тип | почему |
|---|---|
| `text_captcha` | LLM по HTTP (`LLM_ENDPOINT`/`LLM_MODEL`), обучение не нужно |
| `image_to_text`, `math_image` | PaddleOCR / TrOCR из коробки, нужен `--extra cv` |
| `audio_captcha`, `recaptcha_v2` audio | Whisper из коробки |
| `slider` | классическое сопоставление краёв (opencv), модели нет |
| `friendly_captcha`, `altcha` | чистый sha256 по payload |