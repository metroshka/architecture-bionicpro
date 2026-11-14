import type Keycloak from 'keycloak-js';

export interface ReportPeriod {
  dateFrom: string;
  dateTo: string;
}

export interface Report {
  status: 'ready' | 'empty';
  timezone: 'UTC';
  period: { date_from: string; date_to: string };
  row_count: number;
  items: Array<{
    report_date: string;
    prosthesis_id: string;
    model: string;
    movement_count: number;
    avg_response_time_ms: number;
  }>;
}

export class ReportError extends Error {
  constructor(message: string, public readonly needsLogin = false) {
    super(message);
    this.name = 'ReportError';
  }
}

type AuthClient = Pick<Keycloak, 'authenticated' | 'token' | 'updateToken'>;
const DAY_MS = 24 * 60 * 60 * 1000;
const REQUEST_TIMEOUT_MS = 30000;

export function yesterdayUtc(now = new Date()): string {
  return new Date(Date.UTC(now.getUTCFullYear(), now.getUTCMonth(), now.getUTCDate()) - DAY_MS)
    .toISOString().slice(0, 10);
}

function dayTimestamp(value: string): number {
  const result = Date.parse(value + 'T00:00:00Z');
  return /^\d{4}-\d{2}-\d{2}$/.test(value) && Number.isFinite(result)
    && new Date(result).toISOString().slice(0, 10) === value ? result : NaN;
}

export function validatePeriod(period: ReportPeriod, now = new Date()): string | null {
  const start = dayTimestamp(period.dateFrom);
  const end = dayTimestamp(period.dateTo);
  if (!Number.isFinite(start) || !Number.isFinite(end)) {
    return 'Укажите начало и конец периода.';
  }
  if (period.dateFrom < '1970-01-01' || period.dateTo < period.dateFrom) {
    return 'Дата окончания должна быть не раньше даты начала; даты — не ранее 01.01.1970.';
  }
  if ((end - start) / DAY_MS + 1 > 31) {
    return 'Выберите период не длиннее 31 дня, включая обе даты.';
  }
  if (period.dateTo > yesterdayUtc(now)) {
    return 'Выберите только завершённые дни: последний доступный для запроса день — '
      + yesterdayUtc(now) + ' (UTC).';
  }
  return null;
}

async function refreshAccess(auth: AuthClient, minValidity: number): Promise<void> {
  if (!auth.authenticated) {
    throw new ReportError('Для получения отчёта войдите в приложение.', true);
  }
  try {
    await auth.updateToken(minValidity);
  } catch {
    throw new ReportError('Не удалось обновить вход. Повторите попытку или войдите снова.', true);
  }
  if (!auth.token) {
    throw new ReportError('Сеанс завершён. Войдите снова.', true);
  }
}

async function fetchReport(url: string, token: string): Promise<{ status: number; body: any }> {
  const controller = new AbortController();
  const timeout = window.setTimeout(() => controller.abort(), REQUEST_TIMEOUT_MS);
  try {
    const response = await fetch(url, {
      method: 'GET',
      headers: { Authorization: 'Bearer ' + token },
      credentials: 'omit',
      cache: 'no-store',
      redirect: 'error',
      signal: controller.signal,
    });
    // Сохраняем HTTP-код даже при не-JSON ошибке прокси или сервера.
    const body = await response.json().catch((error) => {
      if (controller.signal.aborted) throw error;
      return null;
    });
    return { status: response.status, body };
  } catch {
    throw new ReportError(controller.signal.aborted
      ? 'Сервер не ответил вовремя. Попробуйте скачать отчёт ещё раз.'
      : 'Не удалось связаться с сервисом отчётов. Проверьте соединение и повторите попытку.');
  } finally {
    window.clearTimeout(timeout);
  }
}

export async function requestReport(auth: AuthClient, period: ReportPeriod): Promise<Report> {
  const validationError = validatePeriod(period);
  if (validationError) throw new ReportError(validationError);
  const apiUrl = process.env.REACT_APP_API_URL;
  if (!apiUrl) throw new ReportError('Адрес сервиса отчётов не настроен.');
  const query = new URLSearchParams({ date_from: period.dateFrom, date_to: period.dateTo });
  const url = apiUrl.replace(/\/$/, '') + '/reports?' + query.toString();

  // Токен берётся из адаптера после обновления; в URL владельца и токена нет.
  await refreshAccess(auth, 30);
  let result = await fetchReport(url, auth.token!);
  if (result.status === 401) {
    // Одна повторная попытка на случай истечения токена между проверкой и запросом.
    await refreshAccess(auth, -1);
    result = await fetchReport(url, auth.token!);
  }
  const { status, body } = result;
  if (status === 401) throw new ReportError('Сеанс не принят сервером. Войдите снова.', true);
  if (status === 403) throw new ReportError('Доступ к отчёту запрещён.');
  if (status === 409) {
    const missing: string[] = Array.isArray(body?.missing_dates)
      ? body.missing_dates.filter((day: unknown) =>
        typeof day === 'string' && Number.isFinite(dayTimestamp(day))) : [];
    throw new ReportError('Отчёт пока не готов за весь выбранный период.'
      + (missing.length ? ' Не подготовлены даты (UTC): ' + missing.join(', ') + '.' : '')
      + ' Выберите другой период или повторите запрос позже.');
  }
  if (status === 413) throw new ReportError('Отчёт слишком большой. Сократите период.');
  if (status === 422) throw new ReportError('Сервис отклонил период. Проверьте даты: максимум 31 день.');
  if (status >= 500) throw new ReportError('Сервис отчётов временно недоступен. Повторите запрос позже.');

  if (status !== 200 || !body || !['ready', 'empty'].includes(body.status)
    || body.timezone !== 'UTC' || body.period?.date_from !== period.dateFrom
    || body.period?.date_to !== period.dateTo || !Array.isArray(body.items)
    || body.row_count !== body.items.length
    || (body.status === 'empty') !== (body.row_count === 0)) {
    throw new ReportError('Сервис вернул неожиданный ответ. Файл не скачан; повторите запрос позже.');
  }
  return body as Report;
}

export function downloadReportFile(report: Report): void {
  const blob = new Blob([JSON.stringify(report, null, 2) + '\n'], { type: 'application/json' });
  const objectUrl = URL.createObjectURL(blob);
  const link = document.createElement('a');
  link.href = objectUrl;
  link.download = 'bionicpro-report-' + report.period.date_from + '_' + report.period.date_to + '.json';
  document.body.appendChild(link);
  try {
    link.click();
  } finally {
    link.remove();
    // Даём браузеру начать скачивание до освобождения URL.
    window.setTimeout(() => URL.revokeObjectURL(objectUrl), 1000);
  }
}
