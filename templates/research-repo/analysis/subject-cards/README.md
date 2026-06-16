# Предметные карточки доработок

Этот каталог хранит итерационный слой `subject cards`: кандидаты, реестр, карточки, evidence и gaps.

Базовый цикл:

```bash
python -m one_c_autoresearch subject-card discover
python -m one_c_autoresearch subject-card contour-draft
python -m one_c_autoresearch subject-card contour-validate
python -m one_c_autoresearch subject-card classify
python -m one_c_autoresearch subject-card registry-build
python -m one_c_autoresearch subject-card seed --from-registry
python -m one_c_autoresearch subject-card refine --card <slug>
python -m one_c_autoresearch subject-card validate
python -m one_c_autoresearch review-dashboard build
```

Назначение слоев:

- `candidates.csv` — автогенерируемые гипотезы, не источник истины.
- `contours.csv` — источник истины для предметных контуров: граница переноса,
  доказательства, закрытые runtime/ИБ-проверки и объяснение, почему карточка не
  является технической корзиной.
- `classification.csv` — решения `accept/split/merge/reject/supporting` по кандидатам.
- `registry.csv` — канонический реестр предметных доработок и кандидатов.
- `coverage.csv` — связь BF/detail maps с subject cards или явным статусом `unclassified/technical_support`.
- `cards/<slug>/` — человекочитаемая карточка принятой предметной доработки.

`--subject-card <slug>` строит review-dashboard только по выбранной предметной
карточке и не добавляет в `outputs/review/data.json` общий список технических
detail-map объектов.

## Контракт разделов

`subject-card.json` — это предметный слой для аналитика. Разделы карточки
фиксируют доказанные утверждения по доработке, а не техническую структуру
объектов 1С. Для всех разделов, кроме `open_questions`, строка должна иметь
формат:

```json
{
  "claim": "Что подтверждено по доработке.",
  "source": "analysis/reverse-map/scenarios/BF-000/evidence.csv",
  "line": "BF-000-E001",
  "confidence": "high"
}
```

`open_questions` хранит вопросы с полями `id`, `question`, `why_open`,
`needed`.

Техническая детализация реквизитов, правил формы, проверок заполнения,
жизненного цикла, прав, регламентных заданий, UI и интеграций должна жить в
`analysis/detail-maps/**/*/detail-map.json`. Предметная карточка связывается с
такими картами через `linked_detail_maps`; не дублируйте в `subject-card`
строки формата `object/name/data_type/...`.

## Контурная проверка

`contour-draft` собирает черновик `contours.csv` из final feature map, сценариев
reverse-map, feature evidence, detail-map индекса и закрытых ИБ-проверок.
Агент обязан проверить, что accepted-контур имеет:

- предметное название и связанный BF;
- `scenario_summary`, `migration_boundary`, `why_this_is_one_contour`;
- `why_not_technical_bucket` с явным объяснением, почему это не группировка
  документов, регистров, форм или модулей;
- `evidence_refs`, а при наличии runtime/ИБ-доказательств также `runtime_refs`;
- `technical_bucket_refs`, чтобы технические кандидаты были связаны с контуром
  как поддерживающая подложка.

`contour-validate` и `subject-card validate` должны падать, если все кандидаты
приняты как самостоятельные карточки, если ready-карточка не связана с accepted
контуром, либо если в карточке остался шаблонный текст вместо вывода аналитика.
