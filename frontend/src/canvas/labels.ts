// RU labels + tooltip descriptions for catalog node types and trigger kinds. Supersedes
// displayNames.ts: that map returned the raw key unmodified for anything unmapped (e.g.
// "batch_submit" straight in the UI) — this one always humanizes an unmapped key instead.
const LABELS: Record<string, string> = {
  "market.bump": "Поднять лот",
  "market.reprice": "Изменить цену",
  "market.relist": "Перевыставить",
  "market.auto_reply": "Автоответ",
  condition: "Условие",
  for_each_lot: "Для каждого лота",
  for_each_account: "Для каждого аккаунта",
  get_my_lots: "Мои лоты",
  manual: "Вручную",
  schedule: "По расписанию",
  event: "По событию",
  bool_op: "Логическая операция",
  compare: "Сравнение",
  math: "Математика",
  string_concat: "Склейка строк",
  switch: "Переключатель",
  wait_until: "Ждать до",
  fork: "Разветвление",
  join: "Слияние",
  batch_submit: "Отправить пакет",
  batch_status: "Статус пакета",
  batch_list_pending: "Список ожидающих",
  // wave-06 catalog keys — actual registered NodeType.key values (app/domain/catalog/registry.py),
  // dotted under the "logic" facade, distinct from the bare aliases above.
  "logic.fork": "Разветвление",
  "logic.join": "Слияние",
  "logic.batch": "Пакет шагов",
  "logic.batch_status": "Статус пакета",
  "logic.batch_list_pending": "Список ожидающих",
};

const DESCRIPTIONS: Record<string, string> = {
  "market.bump": "Поднимает лот в выдаче маркета",
  "market.reprice": "Меняет цену лота на новое значение",
  "market.relist": "Снимает и повторно выставляет лот",
  "market.auto_reply": "Отправляет автоответ покупателю",
  condition: "Ветвит поток по true/false условию",
  for_each_lot: "Повторяет вложенные шаги для каждого лота аккаунта",
  for_each_account: "Повторяет вложенные шаги для каждого аккаунта",
  get_my_lots: "Забирает список лотов текущего аккаунта",
  manual: "Запуск флоу вручную из интерфейса",
  schedule: "Запуск флоу по cron-расписанию",
  event: "Запуск флоу по внешнему событию",
  bool_op: "Комбинирует несколько условий через AND/OR/NOT",
  compare: "Сравнивает два значения по выбранному оператору",
  math: "Выполняет арифметическую операцию над числами",
  string_concat: "Склеивает несколько строк в одну",
  switch: "Выбирает ветку по совпадению значения с одним из вариантов",
  wait_until: "Приостанавливает флоу до заданного момента времени",
  fork: "Запускает несколько веток параллельно",
  join: "Дожидается завершения параллельных веток и продолжает поток",
  batch_submit: "Отправляет пакет задач на выполнение",
  batch_status: "Проверяет статус ранее отправленного пакета",
  batch_list_pending: "Возвращает список ещё не завершённых задач пакета",
  "logic.fork": "Запускает несколько веток параллельно",
  "logic.join": "Дожидается завершения параллельных веток и продолжает поток",
  "logic.batch": "Оборачивает вложенные шаги и отправляет их единым пакетом",
  "logic.batch_status": "Проверяет статус ранее отправленного пакета",
  "logic.batch_list_pending": "Возвращает список ещё не завершённых задач пакета",
};

function humanize(raw: string): string {
  return raw
    .split(/[._-]+/)
    .filter(Boolean)
    .map((word) => word.charAt(0).toUpperCase() + word.slice(1))
    .join(" ");
}

/** Facade method (e.g. an action node's underlying "market.bump") takes priority over the
 * node's own type when both are known — that's the label a user recognizes on canvas. */
export function displayLabel(nodeType: string, facadeMethod?: string): string {
  const key = facadeMethod ?? nodeType;
  return LABELS[key] ?? LABELS[nodeType] ?? humanize(key);
}

export function nodeDescription(nodeType: string): string {
  return DESCRIPTIONS[nodeType] ?? "Узел флоу без подробного описания";
}
