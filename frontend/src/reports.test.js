import { downloadReportFile, requestReport, validatePeriod, yesterdayUtc } from './reports';

const period = { dateFrom: '2026-11-08', dateTo: '2026-11-10' };
const ready = {
  status: 'ready', timezone: 'UTC',
  period: { date_from: period.dateFrom, date_to: period.dateTo },
  row_count: 1,
  items: [{ report_date: '2026-11-10', prosthesis_id: '581949c9-9087-543e-aeb8-5b18a9e7e70e',
    model: 'DEMO-Hand-A', movement_count: 3, avg_response_time_ms: 100 }],
};
const reply = (status, body) => ({ status, json: jest.fn().mockResolvedValue(body) });
let auth;

beforeEach(() => {
  jest.useFakeTimers('modern');
  jest.setSystemTime(new Date('2026-11-14T12:00:00Z'));
  process.env.REACT_APP_API_URL = 'http://localhost:8000';
  global.fetch = jest.fn();
  auth = { authenticated: true, token: 'old-access', updateToken: jest.fn().mockResolvedValue(false) };
});

afterEach(() => {
  jest.useRealTimers();
  jest.restoreAllMocks();
});

test('UTC-день и включительный предел: 31 день допустим, 32 — нет', () => {
  expect(yesterdayUtc(new Date('2026-11-14T00:30:00+03:00'))).toBe('2026-11-12');
  expect(validatePeriod({ dateFrom: '2026-10-10', dateTo: '2026-11-09' })).toBeNull();
  expect(validatePeriod({ dateFrom: '2026-10-10', dateTo: '2026-11-10' })).toMatch(/31 дня/);
});

test('пустые, невозможные, обратные и незавершённые даты не вызывают API', async () => {
  const badPeriods = [
    { dateFrom: '', dateTo: '2026-11-10' },
    { dateFrom: '2026-02-30', dateTo: '2026-03-01' },
    { dateFrom: '2026-11-10', dateTo: '2026-11-08' },
    { dateFrom: '2026-11-14', dateTo: '2026-11-14' },
  ];
  for (const invalid of badPeriods) {
    await expect(requestReport(auth, invalid)).rejects.toHaveProperty('name', 'ReportError');
  }
  expect(auth.updateToken).not.toHaveBeenCalled();
  expect(fetch).not.toHaveBeenCalled();
});

test('отправляет обновлённый access-токен и только две даты, возвращает готовый отчёт', async () => {
  auth.updateToken.mockImplementation(async () => { auth.token = 'new-access'; return true; });
  fetch.mockResolvedValue(reply(200, ready));
  await expect(requestReport(auth, period)).resolves.toEqual(ready);
  expect(auth.updateToken).toHaveBeenCalledWith(30);
  const [url, options] = fetch.mock.calls[0];
  expect(url).toBe('http://localhost:8000/reports?date_from=2026-11-08&date_to=2026-11-10');
  expect(options.headers).toEqual({ Authorization: 'Bearer new-access' });
  expect(options).toMatchObject({ credentials: 'omit', cache: 'no-store', redirect: 'error' });
});

test('обработанный пустой период остаётся успешным отчётом', async () => {
  const empty = { ...ready, status: 'empty', row_count: 0, items: [] };
  fetch.mockResolvedValue(reply(200, empty));
  await expect(requestReport(auth, period)).resolves.toEqual(empty);
});

test('409 сообщает недостающие дни и не возвращает объект отчёта', async () => {
  fetch.mockResolvedValue(reply(409, { error: 'period_not_ready', missing_dates: ['2026-11-08'] }));
  await expect(requestReport(auth, period)).rejects.toThrow(/Не подготовлены даты \(UTC\): 2026-11-08/);
  expect(fetch).toHaveBeenCalledTimes(1);
});

test('после 401 один раз обновляет токен принудительно и повторяет запрос', async () => {
  auth.updateToken.mockImplementation(async (validity) => {
    if (validity === -1) auth.token = 'retry-access';
    return validity === -1;
  });
  fetch.mockResolvedValueOnce(reply(401, {})).mockResolvedValueOnce(reply(200, ready));
  await expect(requestReport(auth, period)).resolves.toEqual(ready);
  expect(auth.updateToken.mock.calls).toEqual([[30], [-1]]);
  expect(fetch.mock.calls[1][1].headers.Authorization).toBe('Bearer retry-access');
});

test('повторный 401 предлагает вход и не создаёт бесконечных повторов', async () => {
  fetch.mockResolvedValue(reply(401, {}));
  await expect(requestReport(auth, period)).rejects.toMatchObject({ needsLogin: true });
  expect(fetch).toHaveBeenCalledTimes(2);
});

test('не обращается к API без входа или при ошибке обновления токена', async () => {
  auth.authenticated = false;
  await expect(requestReport(auth, period)).rejects.toMatchObject({ needsLogin: true });
  auth.authenticated = true;
  auth.updateToken.mockRejectedValue(new Error('refresh failed'));
  await expect(requestReport(auth, period)).rejects.toMatchObject({ needsLogin: true });
  expect(fetch).not.toHaveBeenCalled();
});

test('сетевая ошибка и 503, включая не-JSON ответ, не маскируются пустым отчётом', async () => {
  fetch.mockRejectedValueOnce(new TypeError('Failed to fetch'));
  await expect(requestReport(auth, period)).rejects.toThrow(/связаться с сервисом/);
  fetch.mockResolvedValueOnce({ status: 503, json: () => Promise.reject(new SyntaxError('HTML')) });
  await expect(requestReport(auth, period)).rejects.toThrow(/временно недоступен/);
});

test('зависший запрос прерывается через 30 секунд', async () => {
  let started;
  const fetchStarted = new Promise((resolve) => { started = resolve; });
  fetch.mockImplementation((url, { signal }) => new Promise((resolve, reject) => {
    signal.addEventListener('abort', () => reject(new DOMException('Aborted', 'AbortError')));
    started();
  }));
  const request = requestReport(auth, period);
  const assertion = expect(request).rejects.toThrow(/не ответил вовремя/);
  await fetchStarted;
  jest.advanceTimersByTime(30000);
  await assertion;
});

test('не скачивает неожиданный ответ, другой период или противоречивый статус', async () => {
  const badReplies = [
    null,
    { ...ready, period: { date_from: '2026-11-07', date_to: '2026-11-10' } },
    { ...ready, status: 'empty' },
    { ...ready, row_count: 2 },
  ];
  for (const body of badReplies) {
    fetch.mockResolvedValueOnce(reply(200, body));
    await expect(requestReport(auth, period)).rejects.toThrow(/неожиданный ответ/);
  }
});

test('JSON скачивается с именем периода, временная ссылка удаляется', () => {
  URL.createObjectURL = jest.fn().mockReturnValue('blob:report-test');
  URL.revokeObjectURL = jest.fn();
  const click = jest.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(function () {
    expect(this.download).toBe('bionicpro-report-2026-11-08_2026-11-10.json');
    expect(this.href).toBe('blob:report-test');
    expect(document.body.contains(this)).toBe(true);
  });
  downloadReportFile(ready);
  expect(click).toHaveBeenCalledTimes(1);
  expect(URL.createObjectURL.mock.calls[0][0]).toBeInstanceOf(Blob);
  expect(URL.createObjectURL.mock.calls[0][0].type).toBe('application/json');
  expect(document.querySelector('a[download]')).toBeNull();
  expect(URL.revokeObjectURL).not.toHaveBeenCalled();
  jest.advanceTimersByTime(1000);
  expect(URL.revokeObjectURL).toHaveBeenCalledWith('blob:report-test');
});
