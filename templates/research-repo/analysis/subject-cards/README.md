# Предметные карточки доработок

Этот каталог хранит итерационный слой `subject cards`: кандидаты, реестр, карточки, evidence и gaps.

Базовый цикл:

```bash
python -m one_c_autoresearch subject-card discover
python -m one_c_autoresearch subject-card classify
python -m one_c_autoresearch subject-card registry-build
python -m one_c_autoresearch subject-card seed --from-registry
python -m one_c_autoresearch subject-card refine --card <slug>
python -m one_c_autoresearch subject-card validate
python -m one_c_autoresearch review-dashboard build
```

Назначение слоев:

- `candidates.csv` — автогенерируемые гипотезы, не источник истины.
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
